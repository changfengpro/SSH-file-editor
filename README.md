# SSH File Editor

`sfe` is a small terminal editor for SSH sessions. It is intentionally simple: it opens quickly, edits plain text files, and provides useful C completions without requiring `clangd`, `ctags`, or editor plugins.

## Run

```bash
cd "/home/rmer/project/SSH file editor"
python3 sfe.py hello.c
```

Optional shortcut:

```bash
chmod +x sfe.py
ln -sf "$PWD/sfe.py" "$HOME/.local/bin/sfe"
sfe hello.c
```

## Vim-Style Keys

- Starts in Normal mode.
- `i`: enter Insert mode.
- `a`: append after cursor and enter Insert mode.
- `o`: open a new line below and enter Insert mode.
- `Esc`: return to Normal mode or close the completion menu.
- `h`/`j`/`k`/`l` or arrow keys: move in Normal mode.
- `x`: delete the character under the cursor in Normal mode.
- `:w`: save.
- `:q`: quit when there are no unsaved changes.
- `:q!`: quit without saving.
- `:wq` or `:x`: save and quit.
- `Ctrl-O`/`F2`: backup save shortcut.
- `Ctrl-Q`/`F10`: backup quit shortcut.
- `Ctrl-F` or `/`: search.

## Completion

- Completions open automatically in Insert mode while typing identifiers, similar to VS Code.
- `Tab` or `Ctrl-Space`: show completions or move to the next candidate.
- `Enter`: accept the selected completion when the menu is open.

## Completion Sources

- C keywords such as `int`, `return`, `struct`
- Common C standard library symbols such as `printf`, `malloc`, `strlen`
- Current file identifiers such as variables and function names
- Header names in `#include` lines such as `stdio.h` and `stdlib.h`
- Fuzzy matching, so `pt` can still suggest `print_total` and `printf`
- Current file symbols are ranked before generic library and keyword suggestions
