"""端到端验证：认证 → 会话隔离 → 统计 → 任务系统（需后端运行中）。

任务系统是平台级操作：demo2 注册后，匿名与普通登录用户都会被拒绝；
如需验证“管理员可创建/执行任务”，请设置环境变量：
    VERIFY_ADMIN_USERNAME / VERIFY_ADMIN_PASSWORD
"""
from __future__ import annotations

import os
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

# 4. 任务系统（平台级操作）
reg_list = call("GET", "/tasks/registry")  # registry/list 保持公开可读
check(
    "任务注册表齐全",
    {"reindex_documents", "cleanup_checkpoints", "vacuum_documents"}
    <= {r["type"] for r in reg_list},
)

# demo2 已注册 → 匿名与普通用户都不能管理任务
anon_create = c.post(
    BASE + "/tasks",
    json={"name": "bad", "task_type": "cleanup_checkpoints", "schedule": "junk:abc"},
)
check("匿名创建任务被拒绝", anon_create.status_code in (401, 403))
normal_create = c.post(
    BASE + "/tasks",
    json={"name": "smoke", "task_type": "cleanup_checkpoints", "schedule": "interval:60"},
    headers=h,
)
check("非管理员创建任务被拒绝", normal_create.status_code == 403)

# 管理员验证（可选：通过环境变量提供管理员账号）
admin_name = os.environ.get("VERIFY_ADMIN_USERNAME", "").strip()
admin_password = os.environ.get("VERIFY_ADMIN_PASSWORD", "")
admin_h = None
if admin_name and admin_password:
    ar = c.post(
        BASE + "/auth/login", json={"username": admin_name, "password": admin_password}
    )
    if ar.status_code == 200:
        admin_h = {"Authorization": f"Bearer {ar.json()['token']}"}
    else:
        print(f"  !! 管理员登录失败 -> {ar.status_code} {ar.text[:120]}")

if admin_h:
    t = call(
        "POST",
        "/tasks",
        json={"name": "smoke", "task_type": "cleanup_checkpoints", "schedule": "interval:60"},
        headers=admin_h,
    )
    call("POST", f"/tasks/{t['id']}/run", headers=admin_h)
    tasks = call("GET", "/tasks", headers=admin_h)
    mine = [x for x in tasks if x["id"] == t["id"]][0]
    check("任务立即执行成功", mine["last_status"] == "success", f"status={mine['last_status']}")
    call("DELETE", f"/tasks/{t['id']}", headers=admin_h)

    bad_sched = c.post(
        BASE + "/tasks",
        json={"name": "bad", "task_type": "cleanup_checkpoints", "schedule": "junk:abc"},
        headers=admin_h,
    )
    check("非法调度 400", bad_sched.status_code == 400)
else:
    print("  -- 跳过管理员任务验证：设置 VERIFY_ADMIN_USERNAME / VERIFY_ADMIN_PASSWORD 后启用")

print("\n" + ("ALL CHECKS PASSED" if fails == 0 else f"{fails} CHECKS FAILED"))
sys.exit(1 if fails else 0)
