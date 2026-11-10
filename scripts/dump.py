"""측정용 덤프: API를 거치지 않고 services를 직접 불러 items → facts → elements/score → cards+more를 JSON으로 저장한다.

사용법:
  python scripts/dump.py fixtures/set1/rules.txt fixtures/set1/kakao.txt -o out/set1.json
  python scripts/dump.py fixtures/set1 fixtures/set2 -o out/sets.json      # 디렉터리: 안의 rules.(txt|pdf) + kakao.txt
  python scripts/dump.py "fixtures/set*" -o out/sets.json                  # 글롭도 됨(셸이 안 풀어도 스크립트가 푼다)

JSON 맨 위에 prompt_version(prompts/ 5개 sha256 앞 8자리 연결)과 model을 기록한다.
실패한 세트는 error를 남기고 다음 세트로 계속 간다.
"""
import argparse
import glob
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm  # noqa: E402
from app.parsers.docs import parse_doc  # noqa: E402
from app.parsers.kakao import parse_kakao  # noqa: E402
from app.services import elements as elements_service  # noqa: E402
from app.services import facts as facts_service  # noqa: E402
from app.services import items as items_service  # noqa: E402
from app.services import questions as questions_service  # noqa: E402


def run_set(name: str, rules_path: Path, kakao_path: Path) -> dict:
    result: dict = {"name": name, "rules": str(rules_path), "kakao": str(kakao_path), "errors": []}

    def note(stage: str, error):
        if error:
            result["errors"].append({"stage": stage, **error})

    sentences = parse_doc(rules_path.name, rules_path.read_bytes())
    items, evidence, error = items_service.extract_items(sentences)
    note("items", error)

    chat = parse_kakao(kakao_path.read_bytes())
    result["doc_count"] = len(sentences)
    result["chat_count"] = len(chat)

    if items:
        note("facts", facts_service.extract_facts(items, chat))
        note("elements", elements_service.judge_elements(items, sentences, evidence))
        items = elements_service.rank_items(items)
        questions, error = questions_service.generate_questions(items, sentences)
        note("questions", error)
    else:
        questions = []

    result["items"] = items  # 각 fact에 source.ids, 각 element에 evidence가 그대로 들어 있다
    result["item_evidence"] = evidence
    result.update(questions_service.split(questions))
    return result


def resolve_sets(args: list[str]) -> list[tuple[str, Path, Path]]:
    """(rules, kakao) 파일 쌍 하나, 또는 디렉터리/글롭 여러 개 → [(name, rules, kakao)]."""
    paths: list[Path] = []
    for a in args:
        matched = [Path(p) for p in sorted(glob.glob(a))] or [Path(a)]
        paths.extend(matched)

    if len(paths) == 2 and all(p.is_file() for p in paths):
        return [(paths[0].parent.name or "set", paths[0], paths[1])]

    sets = []
    for d in paths:
        if not d.is_dir():
            sets.append((d.name, d, d))  # 아래서 "디렉터리가 아님" error로 기록
            continue
        rules = next((p for p in sorted(d.iterdir()) if p.name.lower().startswith("rules") and p.suffix.lower() in (".txt", ".pdf")), None)
        kakao = next((p for p in sorted(d.iterdir()) if p.name.lower().startswith("kakao") and p.suffix.lower() == ".txt"), None)
        sets.append((d.name, rules or d / "rules.txt", kakao or d / "kakao.txt"))
    return sets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="rules 파일과 kakao 파일 한 쌍, 또는 세트 디렉터리들(글롭 가능)")
    ap.add_argument("-o", "--out", required=True, help="결과 JSON 경로")
    ns = ap.parse_args()

    report = {
        "prompt_version": llm.prompt_version(),
        "model": llm.model_name(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sets": [],
    }
    for name, rules, kakao in resolve_sets(ns.inputs):
        print(f"[{name}] {rules} + {kakao} ...", file=sys.stderr)
        try:
            if not rules.is_file() or not kakao.is_file():
                raise FileNotFoundError(f"rules={rules} kakao={kakao}")
            result = run_set(name, rules, kakao)
        except Exception as e:  # 한 세트가 실패해도 계속 간다
            result = {"name": name, "rules": str(rules), "kakao": str(kakao),
                      "error": {"code": type(e).__name__, "message": str(e)[:300], "trace": traceback.format_exc()[-1500:]}}
        report["sets"].append(result)
        summary = (f"items={len(result.get('items', []))} facts={sum(len(it['facts']) for it in result.get('items', []))} "
                   f"cards={len(result.get('cards', []))} more={len(result.get('more', []))} "
                   f"errors={len(result.get('errors', []))}{' FAILED' if 'error' in result else ''}")
        print(f"[{name}] {summary}", file=sys.stderr)

    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out} (prompt_version={report['prompt_version']}, model={report['model']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
