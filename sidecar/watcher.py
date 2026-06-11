# Artifact Sidecar：监控 /workspace/artifacts，自动上传 S3 并回调平台登记
import os, time, hashlib, mimetypes, urllib.request, json
import boto3

WATCH_DIR = "/workspace/artifacts"
seen = {}

s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                  aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET_KEY"])

def file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def register(name, key, size, mime):
    req = urllib.request.Request(
        os.environ["CALLBACK_URL"], method="POST",
        data=json.dumps({"tenant_id": os.environ["TENANT_ID"],
                         "session_id": os.environ["SESSION_ID"],
                         "name": name, "storage_key": key,
                         "size": size, "mime": mime}).encode(),
        headers={"content-type": "application/json",
                 "x-internal-token": os.environ["INTERNAL_TOKEN"]})
    urllib.request.urlopen(req, timeout=10)

def main():
    os.makedirs(WATCH_DIR, exist_ok=True)
    while True:
        for fn in os.listdir(WATCH_DIR):
            path = os.path.join(WATCH_DIR, fn)
            if not os.path.isfile(path):
                continue
            digest = file_hash(path)
            if seen.get(fn) == digest:
                continue
            mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
            key = (f"tenants/{os.environ['TENANT_ID']}/sessions/"
                   f"{os.environ['SESSION_ID']}/{fn}")
            s3.upload_file(path, os.environ["S3_BUCKET"], key,
                           ExtraArgs={"ContentType": mime})
            try:
                register(fn, key, os.path.getsize(path), mime)
            except Exception as e:
                print("register failed:", e)
            seen[fn] = digest
            print("uploaded:", fn)
        time.sleep(3)

if __name__ == "__main__":
    main()