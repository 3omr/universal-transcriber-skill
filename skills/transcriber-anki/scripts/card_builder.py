#!/usr/bin/env python3
"""
card_builder.py
---------------
Formats extracted concept items into beautiful, responsive, 100% English
Anki card HTML/CSS following the Egyptian Written Model Answer standard.
"""

import html
import re
from typing import Dict, Any, List

# CSS styles embedded directly into Anki card templates
ANKI_CARD_CSS = """
.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 16px;
  text-align: left;
  color: #1e293b;
  background-color: #f8fafc;
  line-height: 1.6;
  padding: 10px;
}

.nightMode .card {
  color: #f1f5f9;
  background-color: #0f172a;
}

.card-container {
  max-width: 650px;
  margin: 0 auto;
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
  padding: 24px;
  border: 1px solid #e2e8f0;
}

.nightMode .card-container {
  background: #1e293b;
  border-color: #334155;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
}

/* Header & Badges */
.card-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  align-items: center;
}

.badge {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 4px 10px;
  border-radius: 6px;
}

.badge-def { background: #dbeafe; color: #1e40af; border: 1px solid #bfdbfe; }
.badge-types { background: #f3e8ff; color: #6b21a8; border: 1px solid #e9d5ff; }
.badge-mechanism { background: #ffedd5; color: #9a3412; border: 1px solid #fed7aa; }
.badge-signs { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }
.badge-ttt { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
.badge-complications { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
.badge-past_exams { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }

.nightMode .badge-def { background: #1e3a8a; color: #93c5fd; border-color: #1e40af; }
.nightMode .badge-types { background: #581c87; color: #d8b4fe; border-color: #6b21a8; }
.nightMode .badge-mechanism { background: #7c2d12; color: #fdba74; border-color: #9a3412; }
.nightMode .badge-signs { background: #075985; color: #7dd3fc; border-color: #0369a1; }
.nightMode .badge-ttt { background: #14532d; color: #86efac; border-color: #166534; }
.nightMode .badge-complications { background: #7f1d1d; color: #fca5a5; border-color: #991b1b; }
.nightMode .badge-past_exams { background: #78350f; color: #fde68a; border-color: #92400e; }

.lecture-tag {
  font-size: 11px;
  color: #64748b;
  margin-left: auto;
  font-weight: 600;
}

.nightMode .lecture-tag {
  color: #94a3b8;
}

/* Front & Back Typography */
.question-title {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 12px 0;
  line-height: 1.4;
}

.nightMode .question-title {
  color: #f8fafc;
}

.question-recap {
  font-size: 15px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 12px;
}

.nightMode .question-recap {
  color: #94a3b8;
}

.card-divider {
  border: 0;
  height: 1px;
  background: #e2e8f0;
  margin: 16px 0;
}

.nightMode .card-divider {
  background: #334155;
}

/* Written Model Answer Lists */
.model-answer-list {
  margin: 0;
  padding-left: 20px;
}

.model-answer-list li {
  margin-bottom: 8px;
  font-size: 15px;
}

.model-answer-list li strong {
  color: #0f172a;
}

.nightMode .model-answer-list li strong {
  color: #38bdf8;
}

/* Warning & Contraindications Box */
.warning-box {
  margin-top: 14px;
  padding: 12px 14px;
  background: #fff1f2;
  border-left: 4px solid #e11d48;
  border-radius: 4px;
  font-size: 14px;
  color: #881337;
}

.nightMode .warning-box {
  background: #4c0519;
  border-left-color: #fb7185;
  color: #ffe4e6;
}

.options-block {
  background: #f1f5f9;
  padding: 12px 16px;
  border-radius: 8px;
  margin-top: 10px;
  font-size: 14px;
}

.nightMode .options-block {
  background: #0f172a;
}
"""


def format_card_front_html(card_item: Dict[str, Any]) -> str:
    """Generates pure English Front Card HTML."""
    category = card_item.get("category", "past_exams")
    category_label = card_item.get("category_label", "High-Yield Medical")
    badge_class = f"badge-{category.lower()}"
    badge_text = card_item.get("badge") or category_label
    lecture = card_item.get("lecture", "")

    front_raw = card_item.get("front", "")
    
    # Check if options are present (MCQ format)
    if "\n\n" in front_raw and any(opt in front_raw for opt in ["a.", "b.", "a)", "b)"]):
        stem_part, opt_part = front_raw.split("\n\n", 1)
        question_html = f'<div class="question-title">{html.escape(stem_part)}</div>'
        options_formatted = "<br>".join(html.escape(l) for l in opt_part.split("\n") if l.strip())
        question_html += f'<div class="options-block">{options_formatted}</div>'
    else:
        question_html = f'<div class="question-title">{html.escape(front_raw)}</div>'

    html_out = f"""<div class="card-container">
  <div class="card-header">
    <span class="badge {badge_class}">{html.escape(badge_text)}</span>
    <span class="lecture-tag">{html.escape(lecture)}</span>
  </div>
  {question_html}
</div>"""
    return html_out


def format_card_back_html(card_item: Dict[str, Any]) -> str:
    """Generates pure English Back Card HTML with Written Model Answer formatting."""
    category = card_item.get("category", "past_exams")
    badge_class = f"badge-{category.lower()}"
    badge_text = card_item.get("badge") or card_item.get("category_label", "Answer")
    lecture = card_item.get("lecture", "")

    front_raw = card_item.get("front", "")
    stem_only = front_raw.split("\n\n")[0] if "\n\n" in front_raw else front_raw

    bullets = card_item.get("back_bullets", [])
    
    # Split contraindications / warnings into special highlight box if present
    normal_bullets = []
    contra_bullets = []
    
    for b in bullets:
        b_clean = b.strip()
        if any(w in b_clean.lower() for w in ["contraindicat", "do not", "avoid emesis", "no gastric lavage"]):
            contra_bullets.append(b_clean)
        else:
            normal_bullets.append(b_clean)

    # Format normal bullets as ordered or bulleted list
    list_items = ""
    for b in normal_bullets:
        # Convert **Key:** to <strong>Key:</strong>
        formatted_b = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html.escape(b))
        list_items += f"<li>{formatted_b}</li>\n"

    answer_html = f'<ol class="model-answer-list">\n{list_items}</ol>' if list_items else ""

    # Add contraindication box if applicable
    if contra_bullets:
        contra_items = "".join(f"<div>• {html.escape(c)}</div>" for c in contra_bullets)
        answer_html += f'<div class="warning-box"><strong>⚠️ Contraindications / Warnings:</strong>{contra_items}</div>'

    html_out = f"""<div class="card-container">
  <div class="card-header">
    <span class="badge {badge_class}">{html.escape(badge_text)}</span>
    <span class="lecture-tag">{html.escape(lecture)}</span>
  </div>
  <div class="question-recap">{html.escape(stem_only)}</div>
  <hr class="card-divider"/>
  <div class="card-back-content">
    {answer_html}
  </div>
</div>"""
    return html_out
