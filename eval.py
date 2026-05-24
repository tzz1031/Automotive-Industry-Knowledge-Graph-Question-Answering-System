
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from qa_neo4j import Neo4jQA


@dataclass
class TestCase:
    question: str
    category: str
    expected_keywords: list[str]
    min_hits: int = 1
    weight: float = 1.0
    forbidden_keywords: list[str] | None = None


@dataclass
class EvalResult:
    question: str
    category: str
    answer: str
    expected_keywords: list[str]
    keyword_hits: list[str]
    forbidden_hits: list[str]
    score: float
    passed: bool
    latency_ms: int
    error: str = ""


TEST_CASES = [
    # 技术分类
    TestCase("有哪些ADAS系统？", "技术分类", ["ADS", "C-Pilot", "ADASSystem"], min_hits=2, weight=1.2),
    TestCase("有哪些电池技术？", "技术分类", ["电池", "BatteryTechnology"], min_hits=1, weight=1.0),
    TestCase("有哪些芯片技术？", "技术分类", ["Chip", "芯片"], min_hits=1, weight=1.0),
    TestCase("有哪些传感器技术？", "技术分类", ["Sensor", "雷达", "传感器"], min_hits=1, weight=1.0),
    TestCase("统计技术分类", "统计", ["技术", "ADAS", "Battery", "Technology"], min_hits=2, weight=1.0),

    # 技术参数
    TestCase("C-Pilot 5.0的算力是多少？", "技术参数", ["C-Pilot 5.0", "算力", "1000"], min_hits=2, weight=1.4),
    TestCase("M3激光雷达的测距能力是多少？", "技术参数", ["M3", "测距", "300m"], min_hits=2, weight=1.4),
    TestCase("M3激光雷达的角分辨率是多少？", "技术参数", ["M3", "角分辨率", "0.05"], min_hits=2, weight=1.2),
    TestCase("720kW独立风道超充系统参数是什么？", "技术参数", ["720kW", "单枪", "600kW", "效率"], min_hits=2, weight=1.2),

    # 企业-技术关系
    TestCase("华为研发了哪些技术？", "企业技术", ["华为", "ADS", "技术"], min_hits=2, weight=1.2),
    TestCase("奇瑞发布了哪些技术？", "企业技术", ["奇瑞", "C-Pilot"], min_hits=1, weight=1.0),
    TestCase("速腾聚创发布了哪些技术？", "企业技术", ["速腾聚创", "M3"], min_hits=1, weight=1.0),

    # 车型-技术/供应链
    TestCase("哪些车用了Orin？", "车型技术", ["Orin", "车型"], min_hits=1, weight=1.2),
    TestCase("哪些车用了骁龙8295？", "车型技术", ["8295", "车型"], min_hits=1, weight=1.2),
    TestCase("有激光雷达的纯电SUV有哪些？", "车型筛选", ["激光雷达", "BEV", "SUV"], min_hits=2, weight=1.2),
    TestCase("宁德时代供应哪些车？", "供应链", ["宁德时代", "车型"], min_hits=1, weight=1.0),

    # 车型详情与对比
    TestCase("小米SU7参数", "车型详情", ["小米", "SU7", "价格", "续航"], min_hits=2, weight=0.8),
    TestCase("小米SU7和蔚来ES8对比", "车型对比", ["小米", "蔚来", "价格", "续航"], min_hits=2, weight=0.8),

    # 日常对话
    TestCase("你好", "日常对话", ["汽车技术", "知识图谱"], min_hits=1, weight=0.5),
    TestCase("你能做什么？", "日常对话", ["ADAS", "Orin", "技术"], min_hits=1, weight=0.5),
]

DEFAULT_FORBIDDEN = ["系统错误", "查询出错", "Traceback", "没有匹配到合适", "暂时没有匹配"]


class Evaluator:
    def __init__(self, cases: list[TestCase], pass_threshold: float = 0.7):
        self.qa = Neo4jQA()
        self.cases = cases
        self.pass_threshold = pass_threshold
        self.results: list[EvalResult] = []

    def run(self) -> dict[str, Any]:
        print(f"开始评测 {len(self.cases)} 个问题...\n")
        for i, case in enumerate(self.cases, 1):
            result = self.evaluate_one(case)
            self.results.append(result)
            status = "PASS" if result.passed else "FAIL"
            hit_text = f"{len(result.keyword_hits)}/{len(case.expected_keywords)}"
            print(
                f"{i:02d}. [{status}] score={result.score:.2f} "
                f"hit={hit_text} time={result.latency_ms}ms | {case.question}"
            )
            if result.error:
                print(f"    error: {result.error}")

        summary = self.summary()
        self.print_summary(summary)
        return summary

    def evaluate_one(self, case: TestCase) -> EvalResult:
        start = time.perf_counter()
        answer = ""
        error = ""
        try:
            answer = self.qa.answer(case.question)
        except Exception as exc:
            error = str(exc)
            answer = ""
        latency_ms = int((time.perf_counter() - start) * 1000)

        expected = case.expected_keywords
        forbidden = case.forbidden_keywords if case.forbidden_keywords is not None else DEFAULT_FORBIDDEN
        hits = [kw for kw in expected if kw.lower() in answer.lower()]
        forbidden_hits = [kw for kw in forbidden if kw.lower() in answer.lower()]

        answer_score = 1.0 if len(answer.strip()) >= 8 and not error else 0.0
        keyword_score = min(len(hits) / max(case.min_hits, 1), 1.0)
        forbidden_penalty = 0.25 * len(forbidden_hits)
        latency_penalty = 0.0
        if latency_ms > 5000:
            latency_penalty = 0.15
        elif latency_ms > 2500:
            latency_penalty = 0.05

        score = 0.35 * answer_score + 0.65 * keyword_score - forbidden_penalty - latency_penalty
        score = max(0.0, min(1.0, round(score, 3)))

        return EvalResult(
            question=case.question,
            category=case.category,
            answer=answer,
            expected_keywords=expected,
            keyword_hits=hits,
            forbidden_hits=forbidden_hits,
            score=score,
            passed=score >= self.pass_threshold,
            latency_ms=latency_ms,
            error=error,
        )

    def summary(self) -> dict[str, Any]:
        if not self.results:
            return {}

        weighted_total = 0.0
        weight_sum = 0.0
        for case, result in zip(self.cases, self.results):
            weighted_total += result.score * case.weight
            weight_sum += case.weight

        latencies = [r.latency_ms for r in self.results]
        pass_count = sum(1 for r in self.results if r.passed)
        error_count = sum(1 for r in self.results if r.error)

        by_category: dict[str, dict[str, Any]] = {}
        for case, result in zip(self.cases, self.results):
            item = by_category.setdefault(
                result.category,
                {"count": 0, "pass": 0, "weighted_score": 0.0, "weight": 0.0, "avg_latency_ms": []},
            )
            item["count"] += 1
            item["pass"] += int(result.passed)
            item["weighted_score"] += result.score * case.weight
            item["weight"] += case.weight
            item["avg_latency_ms"].append(result.latency_ms)

        for item in by_category.values():
            item["score"] = round(item["weighted_score"] / max(item["weight"], 1e-9), 3)
            item["pass_rate"] = round(item["pass"] / max(item["count"], 1), 3)
            item["avg_latency_ms"] = int(statistics.mean(item["avg_latency_ms"]))
            del item["weighted_score"]
            del item["weight"]

        return {
            "total_cases": len(self.results),
            "pass_count": pass_count,
            "pass_rate": round(pass_count / len(self.results), 3),
            "weighted_score": round(weighted_total / max(weight_sum, 1e-9), 3),
            "error_count": error_count,
            "avg_latency_ms": int(statistics.mean(latencies)),
            "p50_latency_ms": int(statistics.median(latencies)),
            "max_latency_ms": max(latencies),
            "by_category": by_category,
        }

    def print_summary(self, summary: dict[str, Any]) -> None:
        print("\n" + "=" * 64)
        print("评测报告")
        print("=" * 64)
        print(f"总问题数: {summary['total_cases']}")
        print(f"通过数:   {summary['pass_count']} ({summary['pass_rate']:.1%})")
        print(f"综合分:   {summary['weighted_score']:.1%}")
        print(f"错误数:   {summary['error_count']}")
        print(
            f"延迟:     avg={summary['avg_latency_ms']}ms, "
            f"p50={summary['p50_latency_ms']}ms, max={summary['max_latency_ms']}ms"
        )

        print("\n按问题类型:")
        for category, item in sorted(summary["by_category"].items()):
            bar = "#" * int(item["score"] * 20)
            print(
                f"  {category:<8} {bar:<20} "
                f"score={item['score']:.0%}, pass={item['pass']}/{item['count']}, "
                f"avg={item['avg_latency_ms']}ms"
            )

        failed = [r for r in self.results if not r.passed]
        if failed:
            print("\n未通过样例:")
            for r in failed:
                print(f"  - {r.question}")
                print(f"    score={r.score}, hit={r.keyword_hits}, forbidden={r.forbidden_hits}, error={r.error or '-'}")
                print(f"    answer={r.answer[:160].replace(chr(10), ' ')}")
        else:
            print("\n全部样例通过。")

    def export_json(self, output: Path, summary: dict[str, Any]) -> None:
        report = {
            "summary": summary,
            "cases": [asdict(case) for case in self.cases],
            "results": [asdict(result) for result in self.results],
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n详细报告已保存: {output}")


def load_cases(path: Path | None) -> list[TestCase]:
    if path is None:
        return TEST_CASES
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [TestCase(**item) for item in raw]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the Neo4j automotive QA system.")
    parser.add_argument("--cases", type=Path, default=None, help="Optional JSON file containing custom test cases.")
    parser.add_argument("--output", type=Path, default=Path("eval_report.json"), help="Output JSON report path.")
    parser.add_argument("--pass-threshold", type=float, default=0.7, help="Score threshold for each case to pass.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = load_cases(args.cases)
    evaluator = Evaluator(cases, pass_threshold=args.pass_threshold)
    summary = evaluator.run()
    evaluator.export_json(args.output, summary)


if __name__ == "__main__":
    main()
