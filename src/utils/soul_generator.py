"""
SOUL.md Generator (Tier 4.5)

Automatically updates the Tool Catalog AND Strategic Focus in SOUL.md.
Incorporates performance-based learning from tge_outcomes.
"""

import os
import re
import logging
import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timezone

# Add project root to path for relative imports
import sys
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.knowledge.supabase_client import get_knowledge_base

logger = logging.getLogger(__name__)

class SoulGenerator:
    """Synchronizes SOUL.md with tools and real-time performance focus."""

    def __init__(self, soul_path: str = None):
        self.project_root = project_root
        self.soul_path = soul_path or str(self.project_root / "deploy" / "openclaw" / "SOUL.md")
        self.skills_dir = self.project_root / "deploy" / "openclaw" / "skills"

    def _get_strategic_focus(self) -> str:
        """Fetch top strategies and recent failures from Supabase."""
        try:
            kb = get_knowledge_base()
            # 1. Fetch top successful patterns
            query = kb.client.table("tge_outcomes").select("actual_pattern").gt("pnl_pct", 0)
            res = query.execute()
            
            pattern_counts = {}
            for row in res.data:
                p = row.get("actual_pattern")
                if p:
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1
            
            top_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            strategies = [f"- **{p}**: Successful in {count} recent trades." for p, count in top_patterns]
            
            # 2. Fetch recent failures
            fail_query = kb.client.table("tge_outcomes").select("token_symbol, notes").lt("pnl_pct", 0).order("created_at", desc=True).limit(3)
            fail_res = fail_query.execute()
            failures = [f"- **{r['token_symbol']}**: {r['notes'] or 'No specific failure reason recorded.'}" for r in fail_res.data]

            focus_md = "## Strategic Focus (Performance-Based)\n\n"
            focus_md += "### 🏆 Winning Patterns\n"
            focus_md += "\n".join(strategies) if strategies else "- No clear winning patterns identified yet."
            focus_md += "\n\n### ⚠️ Recent Failure Patterns\n"
            focus_md += "\n".join(failures) if failures else "- No recent failure patterns identified."
            
            focus_md += f"\n\n_Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
            return focus_md
        except Exception as e:
            logger.error(f"Failed to fetch strategic focus: {e}")
            return "## Strategic Focus\n\n- (Live learning data unavailable)"

    def extract_tools_from_file(self, file_path: str) -> List[Dict[str, str]]:
        """Parse a TS file to find tool names and descriptions."""
        tools = []
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            tool_matches = re.finditer(r'api\.registerTool\s*\(\s*\{([\s\S]*?)\}\s*\);', content)
            for match in tool_matches:
                block = match.group(1)
                name_match = re.search(r'name:\s*["\']([^"\']+)["\']', block)
                desc_match = re.search(r'description:\s*["\']([^"\']+)["\']', block)
                if name_match and desc_match:
                    name = name_match.group(1)
                    desc = desc_match.group(1)
                    desc = re.sub(r'IMPORTANT:.*$', '', desc)
                    desc = desc.replace("Output verbatim.", "").strip()
                    tools.append({"name": name, "description": desc})
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
        return tools

    def generate_table(self, title: str, tools: List[Dict[str, str]]) -> str:
        """Generate a markdown table for a skill category."""
        if not tools: return ""
        lines = [f"### {title} ({len(tools)} tools)", "| Tool | When to use |", "|------|-------------|"]
        for t in tools:
            lines.append(f"| `{t['name']}` | {t['description']} |")
        return "\n".join(lines) + "\n"

    def update_soul_md(self, new_catalog: str, new_focus: str) -> bool:
        """Update catalog and focus sections in SOUL.md."""
        try:
            path = Path(self.soul_path)
            if not path.exists(): return False
            content = path.read_text()
            
            # Update Catalog
            content = re.sub(r"<!-- TOOL_CATALOG_START -->[\s\S]*?<!-- TOOL_CATALOG_END -->", 
                            f"<!-- TOOL_CATALOG_START -->\n{new_catalog}\n<!-- TOOL_CATALOG_END -->", content)
            
            # Update Focus
            if "<!-- STRATEGIC_FOCUS_START -->" in content:
                content = re.sub(r"<!-- STRATEGIC_FOCUS_START -->[\s\S]*?<!-- STRATEGIC_FOCUS_END -->", 
                                f"<!-- STRATEGIC_FOCUS_START -->\n{new_focus}\n<!-- STRATEGIC_FOCUS_END -->", content)
            else:
                # Append if not exists
                content += f"\n\n<!-- STRATEGIC_FOCUS_START -->\n{new_focus}\n<!-- STRATEGIC_FOCUS_END -->\n"
                
            path.write_text(content)
            return True
        except Exception as e:
            logger.error(f"Failed to update SOUL.md: {e}")
            return False

    def sync(self) -> bool:
        """Full sync process."""
        catalog_parts = []
        mapping = {"dacle-execution": "Execution Tools", "dacle-explorer": "Explorer Tools", "dacle-workflow": "Workflow Tools"}
        for dir_name, title in mapping.items():
            skill_file = self.skills_dir / dir_name / "index.ts"
            if skill_file.exists():
                tools = self.extract_tools_from_file(str(skill_file))
                catalog_parts.append(self.generate_table(title, tools))
        
        catalog = "\n".join(catalog_parts)
        focus = self._get_strategic_focus()
        
        return self.update_soul_md(catalog, focus)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from src.utils.config import load_config
    try:
        load_config()
    except RuntimeError:
        pass
    gen = SoulGenerator()
    if gen.sync():
        print("SOUL.md synchronized successfully with Strategic Focus.")
    else:
        print("Failed to sync SOUL.md.")
