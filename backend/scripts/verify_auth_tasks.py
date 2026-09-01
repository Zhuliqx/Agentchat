"""B1/B4/B5 端到端验证：认证 → 会话隔离 → 统计 → 任务系统（需后端运行中）。"""
from __future__ import annotations

import sys
from typing import Any

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://localhost:8000/api"
c = httpx.Client(timeout=30)
fails = 0


def check(name, cond, extra=""):
    global fails
    mark = "OK " if cond else "FAIL"
    if not cond:
        fails += 1
    print(f"{mark} {name} {extra}")


def call(method, path, **kw) -> Any:
    r = c.request(method, BASE + path, **kw)
    if r.status_code >= 400:
        print(f"  !! {method} {path} -> {r.status_code} {r.text[:120]}")
        sys.exit(1)
    return r.json() if r.status_code != 204 else None


# 1. 认证（幂等：已注册则直接登录）
r = c.post(BASE + "/auth/register", json={"username": "demo2", "password": "demo123"})
if r.status_code == 201:
    reg = r.json()
    check("注册", reg["username"] == "demo2")
elif r.status_code == 409:
    check("注册(已存在则跳过)", True)
else:
    print(f"  !! register -> {r.status_code} {r.text[:120]}")
    sys.exit(1)
login = call("POST", "/auth/login", json={"username": "demo2", "password": "demo123"})
check("登录返回 token", bool(login.get("token")))
token = login["token"]
h = {"Authorization": f"Bearer {token}"}
me = call("GET", "/auth/me", headers=h)
check("me 恢复用户", me["username"] == "demo2")
bad = c.post(BASE + "/auth/login", json={"username": "demo2", "password": "x"})
check("错误密码 401", bad.status_code == 401)
noauth = c.get(BASE + "/auth/me")
check("未认证 401", noauth.status_code == 401)

# 2. 会话隔离
s = call("POST", "/sessions", headers=h)
demo_list = call("GET", "/sessions", headers=h)
guest_list = call("GET", "/sessions")
check("用户会话列表含新建", any(x["id"] == s["id"] for x in demo_list))
check("访客看不到用户会话", all(x["id"] != s["id"] for x in guest_list))

# 3. 统计
st = call("GET", f"/sessions/{s['id']}/stats", headers=h)
check("统计 message_count=0", st["message_count"] == 0)
check("统计字段齐全", all(k in st for k in ("rounds", "est_tokens", "duration_sec", "total_chars")))

# 4. 任务系统
reg_list = call("GET", "/tasks/registry")
check(
    "任务注册表齐全",
    {"reindex_documents", "cleanup_checkpoints", "vacuum_documents"}
    <= {r["type"] for r in reg_list},
)
t = call(
    "POST",
    "/tasks",
    json={"name": "smoke", "task_type": "cleanup_checkpoints", "schedule": "interval:60"},
)
call("POST", f"/tasks/{t['id']}/run")
tasks = call("GET", "/tasks")
mine = [x for x in tasks if x["id"] == t["id"]][0]
check("任务立即执行成功", mine["last_status"] == "success", f"status={mine['last_status']}")
call("DELETE", f"/tasks/{t['id']}")

# 非法调度拒绝
bad_sched = c.post(
    BASE + "/tasks",
    json={"name": "bad", "task_type": "cleanup_checkpoints", "schedule": "junk:abc"},
)
check("非法调度 400", bad_sched.status_code == 400)

print("\n" + ("ALL CHECKS PASSED" if fails == 0 else f"{fails} CHECKS FAILED"))
sys.exit(1 if fails else 0)
