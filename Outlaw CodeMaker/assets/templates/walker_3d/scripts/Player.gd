extends CharacterBody3D
## Outlaw template — first-person walker.
## Captures the mouse on _ready; WASD + mouselook. Add a Camera3D under this node.

@export var speed: float = 4.5
@export var mouse_sensitivity: float = 0.0025
@export var gravity: float = 14.0

@onready var camera: Camera3D = $Camera3D if has_node(^"Camera3D") else null


func _ready() -> void:
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		rotation.y -= event.relative.x * mouse_sensitivity
		if camera:
			camera.rotation.x = clamp(
				camera.rotation.x - event.relative.y * mouse_sensitivity,
				-PI / 2 + 0.05, PI / 2 - 0.05,
			)
	elif event.is_action_pressed(&"ui_cancel"):
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y -= gravity * delta

	var direction := Vector3.ZERO
	if Input.is_action_pressed(&"move_forward"):  direction -= transform.basis.z
	if Input.is_action_pressed(&"move_back"):     direction += transform.basis.z
	if Input.is_action_pressed(&"move_left"):     direction -= transform.basis.x
	if Input.is_action_pressed(&"move_right"):    direction += transform.basis.x
	direction.y = 0
	direction = direction.normalized()

	velocity.x = direction.x * speed
	velocity.z = direction.z * speed
	move_and_slide()
