extends CharacterBody2D
## Outlaw template — top-down twin-stick player.
## WASD to move, mouse to aim.

@export var speed: float = 260.0


func _physics_process(_delta: float) -> void:
	var input := Vector2(
		Input.get_axis(&"move_left", &"move_right"),
		Input.get_axis(&"move_up", &"move_down"),
	)
	if input.length_squared() > 1.0:
		input = input.normalized()
	velocity = input * speed
	move_and_slide()

	# Aim at the cursor.
	var mouse_world := get_global_mouse_position()
	look_at(mouse_world)


func fire() -> void:
	# Hook for the agent to wire up the projectile spawner.
	pass
