import asyncio, base64, shlex, time, logging
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.api import core_v1_api
from kubernetes_asyncio.stream import WsApiClient
from app.config import settings

log = logging.getLogger("sandbox")
_kube_loaded = False
_runtime_class_checked = None

async def load_kube():
    global _kube_loaded
    if _kube_loaded:
        return
    if settings.KUBE_IN_CLUSTER:
        config.load_incluster_config()
    else:
        await config.load_kube_config()
    _kube_loaded = True

def ns_for_tenant(tenant_id):
    return f"tenant-{tenant_id[:8]}"

async def resolve_runtime_class():
    """探测 gVisor RuntimeClass；缺失且允许回退则用 runc"""
    global _runtime_class_checked
    if _runtime_class_checked is not None:
        return settings.SANDBOX_RUNTIME_CLASS if _runtime_class_checked else None
    await load_kube()
    node_api = client.NodeV1Api()
    try:
        await node_api.read_runtime_class(settings.SANDBOX_RUNTIME_CLASS)
        _runtime_class_checked = True
        return settings.SANDBOX_RUNTIME_CLASS
    except client.exceptions.ApiException as e:
        if e.status == 404 and settings.SANDBOX_RUNTIME_FALLBACK:
            _runtime_class_checked = False
            log.warning("gVisor not found, falling back to runc")
            return None
        raise

class Sandbox:
    def __init__(self, namespace, pod_name):
        self.namespace, self.pod_name = namespace, pod_name
        self.started_at = time.monotonic()

    async def exec(self, command, timeout=120, container="main"):
        ws = WsApiClient()
        api = core_v1_api.CoreV1Api(api_client=ws)
        try:
            resp = await asyncio.wait_for(
                api.connect_get_namespaced_pod_exec(
                    self.pod_name, self.namespace,
                    command=["bash", "-lc", f"cd /workspace && {command}"],
                    container=container, stderr=True, stdin=False,
                    stdout=True, tty=False),
                timeout=timeout)
            return resp[-16000:] if resp else "(no output)"
        except asyncio.TimeoutError:
            return f"(command timed out after {timeout}s)"
        finally:
            await ws.close()

    async def write_file(self, path, content):
        b64 = base64.b64encode(content.encode()).decode()
        await self.exec(
            f"mkdir -p $(dirname {shlex.quote(path)}) && "
            f"echo {b64} | base64 -d > {shlex.quote(path)}")

    async def read_file(self, path):
        return await self.exec(f"cat {shlex.quote(path)} | head -c 64000")

    async def read_file_bytes(self, path):
        out = await self.exec(f"base64 -w0 {shlex.quote(path)}")
        return base64.b64decode(out.strip())

    def elapsed_seconds(self):
        return int(time.monotonic() - self.started_at)

def build_sandbox_pod(pod_name, tenant_id, session_id, runtime_class,
                      enable_browser=True):
    shared_vol = client.V1VolumeMount(name="workspace", mount_path="/workspace")

    def secctx():
        return client.V1SecurityContext(
            run_as_non_root=True, run_as_user=1000,
            allow_privilege_escalation=False,
            capabilities=client.V1Capabilities(drop=["ALL"]),
            seccomp_profile=None if runtime_class else
                client.V1SeccompProfile(type="RuntimeDefault"))

    containers = [
        client.V1Container(
            name="main", image=settings.SANDBOX_IMAGE,
            command=["sleep", "infinity"], working_dir="/workspace",
            security_context=secctx(),
            resources=client.V1ResourceRequirements(
                requests={"cpu": "250m", "memory": "512Mi"},
                limits={"cpu": "1", "memory": "2560Mi",
                        "ephemeral-storage": "2Gi"}),
            volume_mounts=[shared_vol]),
        client.V1Container(
            name="artifact-sidecar", image=settings.ARTIFACT_SIDECAR_IMAGE,
            env=[client.V1EnvVar("S3_ENDPOINT", settings.S3_ENDPOINT),
                 client.V1EnvVar("S3_ACCESS_KEY", settings.S3_ACCESS_KEY),
                 client.V1EnvVar("S3_SECRET_KEY", settings.S3_SECRET_KEY),
                 client.V1EnvVar("S3_BUCKET", settings.S3_BUCKET),
                 client.V1EnvVar("TENANT_ID", tenant_id),
                 client.V1EnvVar("SESSION_ID", session_id),
                 client.V1EnvVar("CALLBACK_URL",
                                 settings.INTERNAL_API_URL + "/internal/artifacts"),
                 client.V1EnvVar("INTERNAL_TOKEN", settings.INTERNAL_TOKEN)],
            security_context=secctx(),
            resources=client.V1ResourceRequirements(
                limits={"cpu": "200m", "memory": "256Mi"}),
            volume_mounts=[shared_vol]),
    ]
    if enable_browser:
        containers.append(client.V1Container(
            name="browser", image=settings.BROWSER_IMAGE,
            security_context=secctx(),
            resources=client.V1ResourceRequirements(
                limits={"cpu": "1", "memory": "2560Mi"}),
            volume_mounts=[shared_vol]))

    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            labels={"app": "agent-sandbox", "tenant": tenant_id[:8],
                    "session": session_id[:13],
                    "runtime": runtime_class or "runc"},
            annotations={"sandbox/created-at": str(int(time.time()))}),
        spec=client.V1PodSpec(
            runtime_class_name=runtime_class,
            restart_policy="Never",
            automount_service_account_token=False,
            enable_service_links=False,
            host_network=False, host_pid=False, host_ipc=False,
            active_deadline_seconds=settings.SANDBOX_TTL_SECONDS,
            containers=containers,
            volumes=[client.V1Volume(
                name="workspace",
                empty_dir=client.V1EmptyDirVolumeSource(size_limit="4Gi"))]))

class SandboxManager:
    """Session 级沙箱：同一对话内多个 Run 复用（保留文件状态）"""
    _cache: dict = {}

    @classmethod
    async def get_or_create(cls, tenant_id, session_id) -> Sandbox:
        from app.config import settings
        from app.runtime.sandbox_factory import get_sandbox_backend
        from app.runtime.sandbox_local import LocalSandboxAdapter

        key = f"{tenant_id}:{session_id}"
        if key in cls._cache:
            return cls._cache[key]

        backend = get_sandbox_backend()
        if backend is not None and settings.SANDBOX_BACKEND == "local":
            handle = await backend.ensure(tenant_id, session_id)
            sbx = LocalSandboxAdapter(handle, backend)
            cls._cache[key] = sbx
            return sbx

        if backend is not None and settings.SANDBOX_BACKEND == "docker":
            from app.runtime.sandbox_docker import DockerSandboxAdapter
            handle = await backend.ensure(tenant_id, session_id)
            sbx = DockerSandboxAdapter(handle, backend)
            cls._cache[key] = sbx
            return sbx

        await load_kube()
        api = client.CoreV1Api()
        ns = ns_for_tenant(tenant_id)
        await cls._ensure_namespace(api, ns, tenant_id)

        from app.infra.db import SessionLocal
        from app.domain.models import SandboxSession
        from sqlalchemy import select
        async with SessionLocal() as db:
            existing = (await db.execute(select(SandboxSession).where(
                SandboxSession.session_id == session_id,
                SandboxSession.status == "running"))).scalar_one_or_none()
            if existing and await cls._pod_alive(api, ns, existing.pod_name):
                sbx = Sandbox(ns, existing.pod_name)
                cls._cache[key] = sbx
                return sbx
            pod_name = f"sbx-{session_id[:13]}"
            runtime_class = await resolve_runtime_class()
            try:
                await api.create_namespaced_pod(
                    ns, build_sandbox_pod(pod_name, tenant_id, session_id,
                                          runtime_class))
            except client.exceptions.ApiException as e:
                if e.status != 409:
                    raise
            await cls._wait_ready(api, ns, pod_name)
            db.add(SandboxSession(tenant_id=tenant_id, session_id=session_id,
                                  namespace=ns, pod_name=pod_name))
            await db.commit()
        sbx = Sandbox(ns, pod_name)
        cls._cache[key] = sbx
        return sbx

    @classmethod
    async def release_for_run(cls, tenant_id, session_id, usage=None):
        sbx = cls._cache.get(f"{tenant_id}:{session_id}")
        if sbx and usage:
            usage.add_sandbox_seconds(sbx.elapsed_seconds())

    @classmethod
    async def terminate(cls, tenant_id, session_id):
        from app.config import settings
        from app.runtime.sandbox_factory import get_sandbox_backend

        key = f"{tenant_id}:{session_id}"
        backend = get_sandbox_backend()
        if backend is not None and settings.SANDBOX_BACKEND in ("local", "docker"):
            cached = cls._cache.pop(key, None)
            if cached and hasattr(cached, "_handle"):
                await backend.terminate(cached._handle)
            return

        cls._cache.pop(key, None)
        await load_kube()
        api = client.CoreV1Api()
        from app.infra.db import SessionLocal
        from app.domain.models import SandboxSession
        from sqlalchemy import select
        async with SessionLocal() as db:
            row = (await db.execute(select(SandboxSession).where(
                SandboxSession.session_id == session_id))).scalar_one_or_none()
            if row:
                try:
                    await api.delete_namespaced_pod(
                        row.pod_name, row.namespace, grace_period_seconds=0)
                except Exception:
                    pass
                row.status = "terminated"
                await db.commit()

    @staticmethod
    async def _pod_alive(api, ns, pod_name):
        try:
            pod = await api.read_namespaced_pod(pod_name, ns)
            return pod.status.phase == "Running"
        except Exception:
            return False

    @staticmethod
    async def _wait_ready(api, ns, pod_name, timeout=120):
        for _ in range(timeout):
            pod = await api.read_namespaced_pod(pod_name, ns)
            if pod.status.phase == "Running":
                return
            if pod.status.phase in ("Failed", "Succeeded"):
                raise RuntimeError(f"sandbox pod ended: {pod.status.phase}")
            await asyncio.sleep(1)
        raise TimeoutError("sandbox pod not ready in time")

    @staticmethod
    async def _ensure_namespace(api, ns, tenant_id):
        try:
            await api.read_namespace(ns)
            return
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise
        await api.create_namespace(client.V1Namespace(
            metadata=client.V1ObjectMeta(
                name=ns, labels={"tenant": tenant_id[:8],
                                 "managed-by": "agent-platform"})))
        await api.create_namespaced_resource_quota(ns, client.V1ResourceQuota(
            metadata=client.V1ObjectMeta(name="tenant-quota"),
            spec=client.V1ResourceQuotaSpec(hard={
                "pods": "20", "limits.cpu": "8", "limits.memory": "16Gi"})))
        net = client.NetworkingV1Api()
        await net.create_namespaced_network_policy(ns, client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="sandbox-egress-policy"),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(
                    match_labels={"app": "agent-sandbox"}),
                policy_types=["Ingress", "Egress"],
                ingress=[],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        ports=[client.V1NetworkPolicyPort(protocol="UDP", port=53),
                               client.V1NetworkPolicyPort(protocol="TCP", port=53)]),
                    client.V1NetworkPolicyEgressRule(
                        to=[client.V1NetworkPolicyPeer(
                            ip_block=client.V1IPBlock(
                                cidr="0.0.0.0/0",
                                _except=["10.0.0.0/8", "172.16.0.0/12",
                                         "192.168.0.0/16", "169.254.0.0/16"]))]),
                ])))