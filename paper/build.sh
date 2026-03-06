#!/bin/bash
set -e

PAPERDIR="/sessions/serene-keen-wright/mnt/lintgate/paper"
WORKDIR="/sessions/serene-keen-wright"
SRC="$PAPERDIR/specification_complexity_paper.md"
PREPARED="$WORKDIR/paper_prepared.md"
TEMPLATE="$PAPERDIR/template.tex"
FILTER="$PAPERDIR/theorem-filter.lua"
TEX_OUT="$WORKDIR/paper.tex"
PDF_OUT="$PAPERDIR/specification_complexity_paper.pdf"

echo "=== Step 1: Preparing markdown with YAML frontmatter ==="

python3 << 'PYEOF'
import re

with open("/sessions/serene-keen-wright/mnt/lintgate/paper/specification_complexity_paper.md", "r") as f:
    content = f.read()

# Extract title (first # heading)
title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
title = title_match.group(1) if title_match else "Untitled"

# Extract author
author_match = re.search(r'^\*\*(.+?)\*\*', content, re.MULTILINE)
author = author_match.group(1) if author_match else "Anonymous"

# Extract abstract: everything between "## Abstract" and the next "---"
abstract_match = re.search(
    r'## Abstract\s*\n(.*?)\n---',
    content,
    re.DOTALL
)
abstract = abstract_match.group(1).strip() if abstract_match else ""

# Find where "## 1." starts
body_match = re.search(r'^## 1\.', content, re.MULTILINE)
if body_match:
    body = content[body_match.start():]
else:
    body = content

# Convert ## headings to pandoc-friendly format
def strip_section_numbers(text):
    lines = text.split('\n')
    result = []
    for line in lines:
        m = re.match(r'^(#{1,4})\s+(\d+\.[\d.]*)\s+(.+)$', line)
        if m:
            hashes = m.group(1)
            sec_num = m.group(2)
            heading_text = m.group(3)
            result.append(f'{hashes} {heading_text}')
        else:
            result.append(line)
    return '\n'.join(result)

body = strip_section_numbers(body)

# Fix the Lean code block language tag for lstlistings
body = body.replace('```lean', '```{.lean}')

# Convert "## Appendix A: ..." etc. and inject APPENDIX_MARKER
first_appendix = True
lines = body.split('\n')
new_lines = []
for line in lines:
    m = re.match(r'^(#{1,3})\s+Appendix\s+[A-Z]:\s+(.+)$', line)
    if m:
        if first_appendix:
            new_lines.append('<!-- APPENDIX_MARKER -->')
            first_appendix = False
        hashes = m.group(1)
        appendix_title = m.group(2)
        new_lines.append(f'{hashes} {appendix_title}')
    else:
        new_lines.append(line)
body = '\n'.join(new_lines)

# Build the final document with YAML frontmatter
output = f"""---
title: "{title}"
author:
  - {author}
abstract: |
"""

# Indent abstract for YAML
for line in abstract.split('\n'):
    output += f"  {line}\n"

output += """numbersections: true
---

"""
output += body

with open("/sessions/serene-keen-wright/paper_prepared.md", "w") as f:
    f.write(output)

print(f"Title: {title}")
print(f"Author: {author}")
print(f"Abstract length: {len(abstract)} chars")
print(f"Body length: {len(body)} chars")
print("Prepared markdown written.")
PYEOF

echo ""
echo "=== Step 2: Pandoc markdown -> LaTeX ==="
pandoc "$PREPARED" \
  --from markdown+smart+footnotes \
  --to latex \
  --template="$TEMPLATE" \
  --lua-filter="$FILTER" \
  --number-sections \
  --shift-heading-level-by=-1 \
  --standalone \
  --output="$TEX_OUT"

# Post-process: fix longtables and long texttt spans
python3 << 'POSTEOF'
import re

with open("/sessions/serene-keen-wright/paper.tex", "r") as f:
    tex = f.read()

# Fix ALL longtables with lll columns: wrap in \small and widen last column
import re as re2
tables_fixed = 0
for m in reversed(list(re2.finditer(r'\\begin\{longtable\}\[\]\{@\{\}lll@\{\}\}', tex))):
    start = m.start()
    end_match = re2.search(r'\\end\{longtable\}', tex[start:])
    if end_match:
        end = start + end_match.end()
        table = tex[start:end]
        new_table = table.replace(
            r'\begin{longtable}[]{@{}lll@{}}',
            r'{\small' + '\n' + r'\begin{longtable}[]{@{}l l p{6.5cm}@{}}'
        )
        new_table = new_table[:new_table.rfind(r'\end{longtable}')] + r'\end{longtable}' + '\n}'
        tex = tex[:start] + new_table + tex[end:]
        tables_fixed += 1
print(f"Fixed {tables_fixed} longtable(s) with lll columns")

# Fix long \texttt{} spans: add \allowbreak after underscores
def add_breaks_to_long_texttt(text):
    def replacer(m):
        content = m.group(1)
        if len(content) > 35:
            content = content.replace(r'\_', r'\_\allowbreak ')
        return r'\texttt{' + content + '}'
    return re.sub(r'\\texttt\{([^}]+)\}', replacer, text)

tex = add_breaks_to_long_texttt(tex)
print("Added line-break hints to long inline code spans")

with open("/sessions/serene-keen-wright/paper.tex", "w") as f:
    f.write(tex)
POSTEOF

echo "LaTeX written to $TEX_OUT"
echo ""

echo "=== Step 3: LaTeX -> PDF (two passes for refs) ==="
cd "$WORKDIR"
xelatex -interaction=nonstopmode -output-directory="$WORKDIR" paper.tex
xelatex -interaction=nonstopmode -output-directory="$WORKDIR" paper.tex

echo ""
echo "=== Step 4: Copy PDF to output ==="
cp "$WORKDIR/paper.pdf" "$PDF_OUT"
echo "PDF written to $PDF_OUT"

echo ""
echo "=== Done ==="
