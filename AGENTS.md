# Import Guidelines

To ensure consistency across the repository, please order and format imports as follows:

1. **Grouping**
   - Group imports into three sections, separated by a single blank line:
     1. Standard library imports
     2. Third-party library imports
     3. Local package or relative imports

2. **Ordering**
   - Within each group, sort imports alphabetically by module path.
   - Use explicit module paths instead of wildcard imports (`from module import *`).

3. **Formatting**
   - Combine imports from the same module on a single line when possible (e.g., `from module import A, B`).
   - If the imported names do not fit within 100 characters, use parentheses with one import per line and a trailing comma.
   - Keep import statements at the top of the file, after any module-level docstring and before other code.

These guidelines apply to every file within this repository unless overridden by a more specific `AGENTS.md` file in a subdirectory.
