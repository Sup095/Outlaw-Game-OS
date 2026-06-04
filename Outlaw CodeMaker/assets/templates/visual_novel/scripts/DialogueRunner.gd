extends Node
## Outlaw template — minimal dialogue runner for visual novels.
## Lines are loaded as an Array[Dictionary]; advance with Space / Enter / click.

signal line_shown(speaker: String, text: String)
signal dialogue_finished

@export var lines: Array = []

var _index: int = -1


func start(new_lines: Array = []) -> void:
	if not new_lines.is_empty():
		lines = new_lines
	_index = -1
	advance()


func advance() -> void:
	_index += 1
	if _index >= lines.size():
		dialogue_finished.emit()
		return
	var entry: Dictionary = lines[_index]
	var speaker: String = String(entry.get("speaker", ""))
	var text: String = String(entry.get("text", ""))
	line_shown.emit(speaker, text)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed(&"ui_accept"):
		advance()
