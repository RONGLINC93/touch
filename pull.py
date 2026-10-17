#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pull.py - 从 origin/main 拉取最新代码（rebase 方式）
凭据：若 .env 中有 GITHUB_TOKEN 则用它认证（不落盘、不写进 remote URL）；
      否则回退到系统凭据管理器。
"""
import base64
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    data = {}
    if not os.path.exists(path):
        return data
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                data[k.strip()] = v.strip().strip("'").strip('"')
    return data


def git_auth_args():
    env = load_env(os.path.join(ROOT, ".env"))
    tok = env.get("GITHUB_TOKEN", "")
    if tok:
        b64 = base64.b64encode(("x-access-token:" + tok).encode("utf-8")).decode("ascii")
        return ["-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic " + b64], True
    return [], False


def run(cmd):
    print("> " + " ".join(cmd))
    return subprocess.run(cmd).returncode


def main():
    os.chdir(ROOT)
    auth, has_tok = git_auth_args()
    if has_tok:
        print("[凭据] 使用 .env 中的 GITHUB_TOKEN")
    else:
        print("[凭据] .env 中无 GITHUB_TOKEN，使用系统凭据")

    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[错误] 未找到 git，请先安装 Git for Windows 并确认已加入 PATH。")
        return 1

    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        print("[错误] 当前目录不是 git 仓库")
        return 1

    print("=== 从 origin/main 拉取并变基 ===")
    rc = run(["git"] + auth + ["pull", "--rebase", "origin", "main"])
    if rc != 0:
        print("")
        print("[错误] 拉取失败。若存在冲突，请手动处理：")
        print("        查看冲突：git status")
        print("        解决后：  git add -A && git rebase --continue")
        print("        放弃：    git rebase --abort")
        return 1

    print("")
    print("=== 当前状态 ===")
    subprocess.run(["git", "--no-pager", "log", "--oneline", "-5"])
    print("")
    print("[完成] 已同步到远程最新")
    return 0


def pause_close(prompt):
    """让窗口停留以便查看结果：优先等待任意键，失败则休眠 5 秒。"""
    try:
        import msvcrt
        sys.stdout.write("\n" + prompt)
        sys.stdout.flush()
        msvcrt.getch()
    except Exception:
        try:
            input(prompt)
        except Exception:
            time.sleep(5)


def countdown_close(seconds=5):
    """完成后显示说明并倒数 5 4 3 2 1，然后退出。"""
    sys.stdout.write("\n窗口将在 %d 秒后自动关闭：" % seconds)
    sys.stdout.flush()
    for i in range(seconds, 0, -1):
        sys.stdout.write(" %d" % i)
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 0
    except Exception:
        import traceback
        rc = 1
        print("")
        print("[异常] pull.py 运行出错，详情如下：")
        traceback.print_exc()
        pause_close("按任意键关闭窗口...")
        sys.exit(rc)
    countdown_close()
    sys.exit(rc)
