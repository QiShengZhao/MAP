"""安全 DSL 表达式求值器：AST 白名单，杜绝注入"""
import ast
import operator as op

_BIN_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
            ast.Div: op.truediv, ast.Mod: op.mod}
_CMP_OPS = {ast.Gt: op.gt, ast.GtE: op.ge, ast.Lt: op.lt, ast.LtE: op.le,
            ast.Eq: op.eq, ast.NotEq: op.ne}
_FUNCS = {"abs": abs, "min": min, "max": max,
          "rate": lambda cur, prev: (cur / prev) if prev else float("inf"),
          "pct_change": lambda cur, prev:
              ((cur - prev) / prev * 100) if prev else 0.0}

class ExpressionError(Exception): pass


def evaluate(source: str, variables: dict) -> bool:
    return SafeExpression(source).evaluate(variables)

class SafeExpression:
    def __init__(self, source):
        self.source = source
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise ExpressionError(f"syntax error: {e}")
        self._validate(tree.body)
        self._tree = tree.body

    def _validate(self, node):
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self._validate(v)
        elif isinstance(node, ast.UnaryOp) and \
                isinstance(node.op, (ast.Not, ast.USub)):
            self._validate(node.operand)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            self._validate(node.left)
            self._validate(node.right)
        elif isinstance(node, ast.Compare):
            self._validate(node.left)
            for o in node.ops:
                if type(o) not in _CMP_OPS:
                    raise ExpressionError(
                        f"operator not allowed: {type(o).__name__}")
            for c in node.comparators:
                self._validate(c)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or \
                    node.func.id not in _FUNCS:
                raise ExpressionError("only whitelisted functions allowed")
            if node.keywords:
                raise ExpressionError("keyword args not allowed")
            for a in node.args:
                self._validate(a)
        elif isinstance(node, (ast.Name, ast.Constant)):
            if isinstance(node, ast.Constant) and \
                    not isinstance(node.value, (int, float, str, bool)):
                raise ExpressionError("constant type not allowed")
        else:
            raise ExpressionError(f"node not allowed: {type(node).__name__}")

    def evaluate(self, variables):
        return bool(self._eval(self._tree, variables))

    def _eval(self, node, vars_):
        if isinstance(node, ast.BoolOp):
            results = (self._eval(v, vars_) for v in node.values)
            return all(results) if isinstance(node.op, ast.And) else any(results)
        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand, vars_)
            return (not v) if isinstance(node.op, ast.Not) else -v
        if isinstance(node, ast.BinOp):
            return _BIN_OPS[type(node.op)](
                self._eval(node.left, vars_), self._eval(node.right, vars_))
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, vars_)
            for o, comp in zip(node.ops, node.comparators):
                right = self._eval(comp, vars_)
                if not _CMP_OPS[type(o)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            return _FUNCS[node.func.id](
                *(self._eval(a, vars_) for a in node.args))
        if isinstance(node, ast.Name):
            return vars_.get(node.id, 0)
        if isinstance(node, ast.Constant):
            return node.value
        raise ExpressionError("unreachable")
