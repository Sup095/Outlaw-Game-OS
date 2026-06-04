# Project templates

Each subdirectory is a tiny scaffold the **New Game wizard** copies on top of
the default Godot 4 project (`project.godot` + `Main.tscn` + `Main.gd` from
`vision/godot_project.scaffold_files`). Files in a template **overwrite** the
scaffold where they collide, so a template can replace `Main.gd` or
`Main.tscn` if it wants to ship its own entry point.

Adding a new template
---------------------
1. Make a folder here named after the template (lower_snake_case).
2. Drop in any `.gd`, `.tscn`, `.tres`, etc. you want pre-seeded. Paths inside
   the folder mirror the destination project layout (so `scripts/Player.gd`
   here lands at `<project>/scripts/Player.gd`).
3. Add an entry to `TEMPLATES` in `ui/new_game_wizard.py` with a label,
   description, and an optional starter-roadmap block.

What templates intentionally don't do
-------------------------------------
* Ship art assets — keep this directory small (and free of license headaches).
  The agent is expected to wire art in during the design session.
* Include a `.godot/` cache. Godot regenerates it on first import.
* Override `project.godot`. The scaffold's project file is canonical; if you
  need extra settings (autoloads, input actions), append them via a script
  the agent runs in its first turn.

The `empty/` template is intentionally blank — it's the "no extras" option in
the wizard and lets us treat "no template" as just another template, no
branching needed at copy time.
