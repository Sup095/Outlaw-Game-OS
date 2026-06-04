extends CharacterBody2D
## Outlaw template — 2D platformer player.
## Starter physics + jump. Tweak in CodeMaker via the agent.

@export var speed: float = 240.0
@export var jump_velocity: float = -360.0
@export var gravity: float = 980.0
@export var coyote_time_ms: int = 90
@export var jump_buffer_ms: int = 90

var _last_grounded_ms: int = -1
var _last_jump_pressed_ms: int = -1


func _physics_process(delta: float) -> void:
	# Gravity
	if not is_on_floor():
		velocity.y += gravity * delta
	else:
		_last_grounded_ms = Time.get_ticks_msec()

	# Horizontal input
	var direction := Input.get_axis(&"move_left", &"move_right")
	velocity.x = direction * speed

	# Jump buffering + coyote time
	if Input.is_action_just_pressed(&"jump"):
		_last_jump_pressed_ms = Time.get_ticks_msec()

	var can_coyote := _last_grounded_ms >= 0 \
		and Time.get_ticks_msec() - _last_grounded_ms <= coyote_time_ms
	var buffered := _last_jump_pressed_ms >= 0 \
		and Time.get_ticks_msec() - _last_jump_pressed_ms <= jump_buffer_ms

	if buffered and (is_on_floor() or can_coyote):
		velocity.y = jump_velocity
		_last_jump_pressed_ms = -1
		_last_grounded_ms = -1

	move_and_slide()
