#!/bin/bash
set -euo pipefail

# רק בסביבת ווב (remote) — לא להריץ מקומית
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# התקנת תלויות הפרויקט (--ignore-installed לעקיפת חבילות מערכת)
pip install --ignore-installed -r "$CLAUDE_PROJECT_DIR/requirements.txt"

# התקנת כלי פיתוח (לינטר וטסטים)
pip install --ignore-installed flake8 pytest
