"""A pygame window that draws the pointer path model, for looking at how it moves."""

import math
import random
import time

import pygame

from mouse_trajectory import get_final_path_with_fitts_law, get_movement_time_from_fitts_law


WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 760
BACKGROUND = (248, 248, 248)
START_COLOR = (25, 25, 25)
TARGET_FILL = (75, 136, 255)
TARGET_OUTLINE = (18, 54, 120)
DOT_COLOR = (220, 60, 60)
PATH_COLOR = (110, 110, 110)
BUTTON_BG = (30, 30, 30)
BUTTON_TEXT = (255, 255, 255)


class Target:
	def __init__(self, x: int, y: int, width: int, height: int):
		self.x = x
		self.y = y
		self.width = width
		self.height = height

	@property
	def center(self) -> tuple[int, int]:
		return (int(self.x + self.width / 2), int(self.y + self.height / 2))

	@property
	def effective_width(self) -> float:
		return (self.width + self.height) / 2.0

	def random_point_inside(self) -> tuple[int, int]:
		px = random.randint(self.x, self.x + self.width)
		py = random.randint(self.y, self.y + self.height)
		return (px, py)

	def rect(self) -> pygame.Rect:
		return pygame.Rect(self.x, self.y, self.width, self.height)


def generate_target(start: tuple[int, int], margin: int = 70) -> Target:
	width = random.randint(30, 180)
	height = random.randint(30, 180)

	attempts = 0
	while attempts < 2000:
		x = random.randint(margin, WINDOW_WIDTH - width - margin)
		y = random.randint(margin, WINDOW_HEIGHT - height - margin)
		target = Target(x, y, width, height)
		center = target.center
		if math.dist(start, center) > 150:
			return target
		width = random.randint(30, 180)
		height = random.randint(30, 180)
		attempts += 1

	return Target(WINDOW_WIDTH - width - 120, WINDOW_HEIGHT // 2, width, height)


def draw_path_trace(screen: pygame.Surface, path_fn, movement_time: float, steps: int = 320) -> None:
	points = []
	for i in range(steps):
		sample_time =  movement_time * (i / (steps - 1))
		x, y = path_fn(sample_time)
		points.append((int(x), int(y)))

	if len(points) > 1:
		pygame.draw.lines(screen, PATH_COLOR, False, points, 2)


def main() -> None:
	pygame.init()
	screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
	pygame.display.set_caption("Fitts Law Target Acquisition")
	clock = pygame.time.Clock()
	font = pygame.font.SysFont("Segoe UI", 20)
	small_font = pygame.font.SysFont("Segoe UI", 18)

	start_position = (150, 520)
	current_position = start_position
	current_target = generate_target(current_position)
	current_end = current_target.random_point_inside()
	target_width = current_target.effective_width

	move_start_time = time.monotonic()
	path_fn = get_final_path_with_fitts_law(target_width, current_position, current_end)
	movement_time = get_movement_time_from_fitts_law(math.dist(current_position, current_end), target_width)
	state = "moving"

	replay_button = pygame.Rect(580, 40, 220, 54)
	next_button = pygame.Rect(820, 40, 220, 54)

	while True:
		for event in pygame.event.get():
			if event.type == pygame.QUIT:
				pygame.quit()
				return

			if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
				if state == "waiting":
					if replay_button.collidepoint(event.pos):
						current_position = start_position
						move_start_time = time.monotonic()
						state = "moving"
					elif next_button.collidepoint(event.pos):
						current_position = start_position
						current_target = generate_target(current_position)
						current_end = current_target.random_point_inside()
						target_width = current_target.effective_width
						move_start_time = time.monotonic()
						path_fn = get_final_path_with_fitts_law(target_width, current_position, current_end)
						movement_time = get_movement_time_from_fitts_law(math.dist(current_position, current_end), target_width)
						state = "moving"

		screen.fill(BACKGROUND)

		if state == "moving":
			elapsed = time.monotonic() - move_start_time
			current_position = path_fn(elapsed)
			if elapsed >= movement_time:
				current_position = current_end
				state = "waiting"

		target_rect = current_target.rect()
		pygame.draw.rect(screen, TARGET_FILL, target_rect, border_radius=6)
		pygame.draw.rect(screen, TARGET_OUTLINE, target_rect, 3, border_radius=6)

		draw_path_trace(screen, path_fn, movement_time)
		pygame.draw.circle(screen, DOT_COLOR, (int(current_position[0]), int(current_position[1])), 8)

		if state == "waiting":
			pygame.draw.rect(screen, BUTTON_BG, replay_button, border_radius=10)
			replay_text = font.render("Replay", True, BUTTON_TEXT)
			screen.blit(replay_text, (replay_button.x + 68, replay_button.y + 12))

			pygame.draw.rect(screen, BUTTON_BG, next_button, border_radius=10)
			button_text = font.render("Next target", True, BUTTON_TEXT)
			screen.blit(button_text, (next_button.x + 45, next_button.y + 12))

			prompt = small_font.render("Target reached. Replay or continue to next target.", True, (30, 30, 30))
			screen.blit(prompt, (35, 35))

		label = small_font.render(
			f"Target W = (H + W)/2 = {target_width:.1f}px   D = {math.dist(start_position, current_end):.1f}px",
			True,
			(35, 35, 35),
		)
		screen.blit(label, (30, 95))

		pygame.display.flip()
		clock.tick(60)


if __name__ == "__main__":
	main()
