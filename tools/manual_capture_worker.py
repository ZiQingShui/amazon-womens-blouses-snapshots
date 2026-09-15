from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[1]
DEFAULT_CONFIG = ROOT / "manual-capture-worker.local.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["siteUrl", "workerToken", "threadId", "codexCommand"]
    missing = [key for key in required if not str(data.get(key, "")).strip()]
    if missing:
        raise SystemExit(f"本机处理器配置缺少：{', '.join(missing)}")
    data["siteUrl"] = str(data["siteUrl"]).rstrip("/")
    data.setdefault("pollSeconds", 2)
    data.setdefault("staleMinutes", 60)
    data.setdefault("workerId", "windows-local-ecomtool")
    return data


def api(config: dict, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config["siteUrl"] + path,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['workerToken']}",
            "User-Agent": USER_AGENT,
            "Referer": config["siteUrl"] + "/",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def update_request(config: dict, request_id: str, status: str, message: str) -> dict:
    return api(config, f"/api/capture-requests/{request_id}", "PATCH", {"status": status, "message": message})


def heartbeat(config: dict, state: str, request_id: str | None = None, message: str = "") -> None:
    api(config, "/api/capture-worker", "POST", {
        "workerId": config["workerId"],
        "state": state,
        "activeRequestId": request_id,
        "message": message,
    })


def request_age_minutes(request: dict) -> float:
    requested = datetime.strptime(request["requestedAt"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - requested).total_seconds() / 60


def task_prompt(request: dict) -> str:
    source = request.get("rankingSource", "official")
    return (
        "这是由公开看板‘立即抓取’按钮触发的手动采集任务，不是定时任务。\n"
        f"请求 ID：{request['id']}\n类目节点：{request['categoryNode']}\n"
        f"榜单：{request['ranking']}\n快照日期：{request['requestedDate']}\n榜单来源：{source}\n\n"
        "请读取 published-dashboard/README.md 和 published-dashboard/tools/manual_capture_task.md，"
        "调用当前可用的 Ecomtool MCP 完成真实采集、校验、生成快照并发布到现有 Sites 看板。"
        "每日自动抓取必须继续保持暂停。处理完成后务必按任务说明把该请求更新为 completed；"
        "遇到登录、验证码、图片缺失、名次缺失或发布失败时更新为 failed，并写明原因。"
    )


def queue_codex(config: dict, request: dict) -> None:
    command = [
        config["codexCommand"], "queue",
        "--thread", config["threadId"],
        "--message", task_prompt(request),
        "-C", str(ROOT.parent),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "Codex 任务唤醒失败").strip()
        raise RuntimeError(detail[-500:])


def run(config: dict) -> None:
    logging.info("本机手动采集处理器已启动，只在按钮产生任务时唤醒 Codex")
    active_id = None
    heartbeat_due = 0.0
    while True:
        try:
            now = time.monotonic()
            if now >= heartbeat_due:
                heartbeat(config, "busy" if active_id else "idle", active_id, "正在处理手动抓取" if active_id else "可以立即抓取")
                heartbeat_due = now + 10
            if active_id:
                current = api(config, f"/api/capture-requests/{active_id}").get("request", {})
                if current.get("status") in {"completed", "failed"}:
                    logging.info("任务 %s 已结束：%s", active_id, current.get("status"))
                    active_id = None
                    heartbeat_due = 0
                time.sleep(config["pollSeconds"])
                continue
            pending = api(config, "/api/capture-requests?status=pending").get("requests", [])
            if not pending:
                time.sleep(config["pollSeconds"])
                continue
            request = pending[0]
            request_id = request["id"]
            update_request(config, request_id, "running", "本机处理器已接单")
            if request.get("rankingSource", "official") != "upload" and request_age_minutes(request) > float(config["staleMinutes"]):
                update_request(config, request_id, "failed", "这是处理器上线前遗留的旧任务，请重新点击立即抓取")
                logging.info("已关闭遗留任务 %s", request_id)
                continue
            try:
                queue_codex(config, request)
            except Exception as error:
                update_request(config, request_id, "failed", f"无法唤醒 Codex：{error}"[:240])
                raise
            active_id = request_id
            heartbeat_due = 0
            logging.info("任务 %s 已发送给 Codex", request_id)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError) as error:
            logging.warning("处理器暂时无法工作：%s", error)
            time.sleep(max(5, config["pollSeconds"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="只处理按钮触发任务的本机采集器")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    log_path = ROOT / "manual-capture-worker.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    run(config)


if __name__ == "__main__":
    main()
