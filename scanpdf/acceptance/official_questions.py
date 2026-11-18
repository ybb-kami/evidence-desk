"""Replay six official Chinese questions and audit their evidence against the supplied PDFs."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import unicodedata

import fitz

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine import find_evidence, parse_pdf  # noqa: E402


QUESTIONS = (
    "论文Attention Is All You Need的第6作者是谁，在哪个公司/机构工作？",
    "其中Adam 优化器的超参数 β₁ 和 β₂ 分别被设置为了多少？",
    "这篇论文中提到的 Transformer 架构，在训练 Base 模型时使用了多少个 GPU？具体是什么型号？",
    "Transformer (big) 模型在 English-to-German 任务上的 BLEU 分数是多少？当时的 State-of-the-art 模型是多少？提升了多少？",
    "请列出论文中引用的关于 “Layer Normalization” 的参考文献（References）的完整作者和年份。",
    "根据《Attention Is All You Need》和《BERT: Pre-training of Deep Bidirectional Transformers "
    "for Language Understanding》。请对比：BERT 的架构和原始 Transformer 的 Encoder 有什么"
    "承袭关系？BERT 丢弃了原始 Transformer 的哪个部分？",
)
ATTENTION = {"v1": "1706.03762v1.pdf", "v7": "1706.03762v7.pdf"}
BERT = "N19-1423.pdf"


@dataclass(frozen=True)
class Requirement:
    label: str
    page: int
    block: int
    patterns: tuple[str, ...]
    required: bool = True

    def matches(self, text):
        text = re.sub(r"(?<=\w)- *\n\s*(?=\w)", "", text)
        text = normalized(text)
        return all(re.search(pattern, text, re.IGNORECASE) for pattern in self.patterns)


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def attention_requirements(version):
    byline, footnote, hardware, adam, metric, citation, encoder_page, encoder_block = {
        "v1": (7, 15, 3, 5, (7, 19), 7, 2, 12),
        "v7": (8, 13, 9, 11, (8, 16), 13, 3, 4),
    }[version]
    return (
        (
            Requirement("第六位作者及署名机构", 1, byline,
                        (r"^Aidan N\. Gomez[^a-z]*†\s+University of Toronto",)),
            Requirement("† 说明研究完成时所在机构", 1, footnote,
                        (r"†Work performed while at Google Brain\.",), required=False),
        ),
        (Requirement("Adam 段落同时明确 β₁ 和 β₂", 7, adam,
                     (r"Adam optimizer.*β1\s*=\s*0\.9,\s*β2\s*=\s*0\.98\b",)),),
        (Requirement("Base 训练硬件与 GPU 数量、型号", 7, hardware,
                     (r"trained our models.*\b8 NVIDIA P100 GPUs\b.*our base models",)),),
        (
            Requirement("英德翻译成绩与相对先前最佳的提升", *metric,
                        (r"English-to-German translation task", r"Transformer \(big\)",
                         r"best previously reported models", r"more than 2\.0 BLEU",
                         r"BLEU score of 28\.4\b")),
            Requirement("表 2 的英德与英法任务语境", 8, 1,
                        (r"Table 2:.*BLEU.*English-to-German and English-to-French",)),
            Requirement("表 2 的先前最佳英德比较行", 8, 9,
                        (r"^ConvS2S Ensemble\s+\[9\]\s+26\.36\b",)),
        ),
        (Requirement("Layer Normalization 参考文献完整作者及年份", 10, citation,
                     (r"^\[1\]\s+Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton\.",
                      r"Layer normalization\..*arXiv:1607\.06450, 2016\.")),),
        (
            Requirement("原始 Transformer 的 Encoder", encoder_page, encoder_block,
                        (r"^Encoder:.*stack of N = 6 identical layers",
                         r"multi-head self-attention", r"feed-forward network")),
            Requirement("原始 Transformer 独立的 Decoder", encoder_page, encoder_block + 1,
                        (r"^Decoder:.*stack of N = 6 identical layers",
                         r"performs multi-head attention over the output of the encoder stack")),
        ),
    )


BERT_REQUIREMENTS = (
    Requirement("BERT 明确沿用多层双向 Transformer Encoder", 3, 26,
                (r"BERT.s model architecture is a multi-layer bidirectional Transformer encoder",
                 r"based on the original implementation described in Vaswani et al\. \(2017\)")),
)


def read_source(path):
    data = path.read_bytes()
    document = parse_pdf(data, path.name)
    with fitz.open(stream=data, filetype="pdf") as pdf:
        blocks = {
            (page.number + 1, block[5] + 1): block[4]
            for page in pdf
            for block in page.get_text("blocks", flags=fitz.TEXTFLAGS_BLOCKS, sort=True)
            if block[6] == 0
        }
    return document, blocks


def audit(document, blocks, index, required, show_hits):
    # Both search and the per-paper branches of compare use this function.
    hits = find_evidence([document], QUESTIONS[index])
    checked_hits = []
    for rank, hit in enumerate(hits, 1):
        anchor = (hit["page"], hit["block"])
        source_text = blocks.get(anchor, "")
        verified = (hit["id"] == document["id"] and hit["name"] == document["name"]
                    and bool(normalized(hit["text"]))
                    and normalized(hit["text"]) in normalized(source_text))
        row = {"rank": rank, "page": anchor[0], "block": anchor[1], "verbatim": verified}
        if show_hits:
            row["text"] = hit["text"]
        checked_hits.append(row)

    criteria = []
    for requirement in required:
        anchor = requirement.page, requirement.block
        if not requirement.matches(blocks.get(anchor, "")):
            raise ValueError(f"{document['name']} gold excerpt changed at page/block {anchor}: {requirement.label}")
        matching_ranks = [
            row["rank"] for hit, row in zip(hits, checked_hits)
            if (row["page"], row["block"]) == anchor and row["verbatim"]
            and requirement.matches(hit["text"])
        ]
        criteria.append({
            "criterion": requirement.label, "page": anchor[0], "block": anchor[1],
            "matched_ranks": matching_ranks, "required": requirement.required,
        })
    required_criteria = [criterion for criterion in criteria if criterion["required"]]
    found = sum(bool(criterion["matched_ranks"]) for criterion in required_criteria)
    return {
        "file": document["name"], "sha256": document["id"],
        "hits": checked_hits, "verified_criteria": found, "required_criteria": len(required_criteria),
        "criteria": criteria,
    }


def run(folder, show_hits=False):
    sources = {name: read_source(folder / name) for name in (*ATTENTION.values(), BERT)}
    cases = []
    for version, name in ATTENTION.items():
        for index, required in enumerate(attention_requirements(version)):
            observations = [audit(*sources[name], index, required, show_hits)]
            if index == 5:
                observations.append(audit(*sources[BERT], index, BERT_REQUIREMENTS, show_hits))
            found = sum(row["verified_criteria"] for row in observations)
            expected = sum(row["required_criteria"] for row in observations)
            cases.append({
                "version": version, "question_number": index + 1, "question": QUESTIONS[index],
                "status": "complete" if found == expected else "partial" if found else "missing",
                "documents": observations,
            })
    return {
        "scope": "evidence retrieval with explicitly selected PDFs; not generated-answer correctness",
        "summary": {
            status: sum(case["status"] == status for case in cases)
            for status in ("complete", "partial", "missing")
        },
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--show-hits", action="store_true", help="Print returned evidence text as well as locators")
    args = parser.parse_args()
    print(json.dumps(run(args.pdf_dir, args.show_hits), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
