"""
SOUL.md Generator (Tier 4.5)

Automatically updates the Tool Catalog in SOUL.md by parsing TypeScript skill files.
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SoulGenerator:
    """Synchronizes SOUL.md Tool Catalog with actual registered tools."""

    def __init__(self, soul_path: str = None):
        self.project_root = Path(__file__).parent.parent.parent
        self.soul_path = soul_path or str(self.project_root / "deploy" / "openclaw" / "SOUL.md")
        self.skills_dir = self.project_root / "deploy" / "openclaw" / "skills"

    def extract_tools_from_file(self, file_path: str) -> List[Dict[str, str]]:
        """Parse a TS file to find tool names and descriptions."""
        tools = []
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            # Find api.registerTool({ ... }) blocks
            # Handle potential newlines between api.registerTool and the opening brace
            tool_matches = re.finditer(r'api\.registerTool\s*\(\s*\{([\s\S]*?)\}\s*\);', content)
            
            for match in tool_matches:
                block = match.group(1)
                
                # Extract name: "..." or name: '...'
                name_match = re.search(r'name:\s*["\']([^"\']+)["\']', block)
                # Extract description: "..." or description: '...'
                desc_match = re.search(r'description:\s*["\']([^"\']+)["\']', block)
                
                if name_match and desc_match:
                    name = name_match.group(1)
                    desc = desc_match.group(1)
                    
                    # Clean up description (remove 'IMPORTANT: ...' or 'Output verbatim.')
                    desc = re.sub(r'IMPORTANT:.*$', '', desc)
                    desc = desc.replace("Output verbatim.", "").strip()
                    
                    tools.append({"name": name, "description": desc})
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            
        return tools

    def generate_table(self, title: str, tools: List[Dict[str, str]]) -> str:
        """Generate a markdown table for a skill category."""
        if not tools:
            return ""
            
        lines = [
            f"### {title} ({len(tools)} tools)",
            "| Tool | When to use |",
            "|------|-------------|"
        ]
        
        for t in tools:
            lines.append(f"| `{t['name']}` | {t['description']} |")
            
        return "\n".join(lines) + "\n"

    def update_soul_md(self, new_catalog: str) -> bool:
        """Replace the catalog section in SOUL.md between markers."""
        try:
            path = Path(self.soul_path)
            if not path.exists():
                logger.error(f"SOUL.md not found at {self.soul_path}")
                return False
                
            content = path.read_text()
            
            start_marker = "<!-- TOOL_CATALOG_START -->"
            end_marker = "<!-- TOOL_CATALOG_END -->"
            
            if start_marker not in content or end_marker not in content:
                logger.error("Markers not found in SOUL.md")
                return False
                
            pattern = f"{start_marker}[\\s\\S]*?{end_marker}"
            replacement = f"{start_marker}\n{new_catalog}\n{end_marker}"
            
            updated_content = re.sub(pattern, replacement, content)
            path.write_text(updated_content)
            return True
        except Exception as e:
            logger.error(f"Failed to update SOUL.md: {e}")
            return False

    def sync(self) -> bool:
        """Full sync process."""
        catalog_parts = []
        
        # Map skill directories to titles
        mapping = {
            "dacle-execution": "Execution Tools",
            "dacle-explorer": "Explorer Tools",
            "dacle-workflow": "Workflow Tools"
        }
        
        for dir_name, title in mapping.items():
            skill_file = self.skills_dir / dir_name / "index.ts"
            if skill_file.exists():
                tools = self.extract_tools_from_file(str(skill_file))
                catalog_parts.append(self.generate_table(title, tools))
        
        if not catalog_parts:
            logger.warning("No tools extracted, skipping update.")
            return False
            
        return self.update_soul_md("\n".join(catalog_parts))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = SoulGenerator()
    if gen.sync():
        print("SOUL.md Tool Catalog synchronized successfully.")
    else:
        print("Failed to sync SOUL.md.")
