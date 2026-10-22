"""시험을 한 번에 돌린다: python tests/run_all.py  (실제 구글 시험까지: --google, 사용자 허락을 받고)

각 시험 스크립트를 따로 띄우고(임시 폴더 하나씩), 마지막 줄 "N/M 통과"를 모아 보여 준다.
하나라도 실패하면 종료 코드가 1이다(GitHub Actions에서 빨갛게 보인다)."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ["t_all", "t_more", "t_repeat", "t_fixes", "t_mood", "t_bugs"]  # 가짜 구글로 도는 시험
LIVE = ["t_google"]                                  # 실제 구글 계정을 쓰는 시험


def run(name, workdir):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen", QT_QPA_FONTDIR="C:/Windows/Fonts")
    proc = subprocess.run([sys.executable, "-u", f"tests/{name}.py", str(workdir)], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout + proc.stderr
    fails = [line for line in out.splitlines() if line.startswith("FAIL ")]
    m = re.search(r"(\d+)/(\d+) 통과\s*$", proc.stdout.strip())
    ok = proc.returncode == 0 and m is not None and m.group(1) == m.group(2) and not fails
    return ok, (f"{m.group(1)}/{m.group(2)}" if m else "끝까지 돌지 못함"), fails, out


def main():
    names = SUITES + (LIVE if "--google" in sys.argv else [])
    total_ok = True
    with tempfile.TemporaryDirectory(prefix="xetical-test-") as tmp:
        for name in names:
            work = Path(tmp) / name
            work.mkdir()
            ok, score, fails, out = run(name, work)
            print(f"{'통과' if ok else '실패'}  {name:<10} {score}", flush=True)
            for line in fails:
                print("      " + line)
            if not ok and not fails:  # 도중에 죽었으면 마지막 출력으로 이유를 보여 준다
                print("\n".join("      " + x for x in out.strip().splitlines()[-15:]))
            total_ok &= ok
    print("\n모두 통과" if total_ok else "\n실패가 있습니다")
    return 0 if total_ok else 1


if __name__ == "__main__":
    sys.exit(main())
