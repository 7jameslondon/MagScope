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

# Circular Import Safety

- When adjusting imports, double-check that reordering does not change when modules are first executed. Many packages (notably `magscope.gui`) rely on specific initialization order, so moving an import between groups can introduce circular dependencies.
- Prefer importing from a module that defines the symbol directly instead of going through package-level re-exports when there is any risk of a cycle (e.g., import `AcquisitionMode` from `magscope.utils`, not from `magscope`).
- If you must refactor imports across modules, run `python -c "import magscope"` locally to confirm no circular import errors are introduced.
