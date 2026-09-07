"""Display a Bezier path with an optional, temporary distortion overlay."""

import pygame

from mouse_trajectory import get_bezier_path, get_distorted_bezier_path


WINDOW_SIZE = (1100, 700)
BACKGROUND = (246, 243, 236)
PATH_COLOR = (38, 42, 47)
DISTORTED_PATH_COLOR = (207, 76, 61)
POINT_COLOR = (43, 116, 96)
CONTROL_COLOR = (143, 151, 158)
BUTTON_COLOR = (38, 42, 47)
BUTTON_HOVER_COLOR = (58, 64, 70)
BUTTON_TEXT_COLOR = (255, 255, 255)

Point = tuple[int, int]


def make_paths() -> tuple[callable, callable]:
	"""Return independent paths with the same start and end points."""
	start = (150, 535)
	end = (950, 535)
	base_path = get_bezier_path(start, end, intermediate_radius_interval=(150, 210))
	distorted_path = get_distorted_bezier_path(
		start,
		end,
		intermediate_radius_interval=(150, 210),
		distortion_zone_time_length=0.08,
		distortion_frequency=1.0,
		deviation_interval=(10, 18),
	)

	return base_path, distorted_path


def sample_path(path: callable, steps: int = 360) -> list[Point]:
	return [
		(round(point[0]), round(point[1]))
		for point in (path(index / (steps - 1)) for index in range(steps))
	]


def draw_label(screen: pygame.Surface, font: pygame.font.Font, text: str, position: tuple[int, int], color: tuple[int, int, int]) -> None:
	screen.blit(font.render(text, True, color), position)


def main() -> None:
	pygame.init()
	screen = pygame.display.set_mode(WINDOW_SIZE)
	pygame.display.set_caption("Bezier Path Distortion")
	clock = pygame.time.Clock()
	title_font = pygame.font.SysFont("Segoe UI", 28, bold=True)
	body_font = pygame.font.SysFont("Segoe UI", 19)
	button_font = pygame.font.SysFont("Segoe UI", 18, bold=True)

	base_path, distorted_path = make_paths()
	base_points = sample_path(base_path)
	distorted_points = sample_path(distorted_path)
	show_distorted = False
	button = pygame.Rect(405, 595, 290, 52)

	running = True
	while running:
		for event in pygame.event.get():
			if event.type == pygame.QUIT:
				running = False
			elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
				if button.collidepoint(event.pos):
					show_distorted = not show_distorted

		screen.fill(BACKGROUND)
		draw_label(screen, title_font, "Bezier path comparison", (38, 28), PATH_COLOR)
		draw_label(
			screen,
			body_font,
			"The red path adds temporary offsets to the same underlying curve.",
			(40, 70),
			(91, 97, 102),
		)

		pygame.draw.lines(screen, (184, 188, 190), False, base_points, 1)
		pygame.draw.lines(screen, DISTORTED_PATH_COLOR if show_distorted else PATH_COLOR, False, distorted_points if show_distorted else base_points, 4)
		pygame.draw.circle(screen, POINT_COLOR, base_points[0], 10)
		pygame.draw.circle(screen, POINT_COLOR, base_points[-1], 10)

		draw_label(screen, body_font, "A", (base_points[0][0] - 8, base_points[0][1] + 18), PATH_COLOR)
		draw_label(screen, body_font, "B", (base_points[-1][0] - 8, base_points[-1][1] + 18), PATH_COLOR)
		draw_label(screen, body_font, "DISTORTED" if show_distorted else "UNDISTORTED", (20, 535), DISTORTED_PATH_COLOR if show_distorted else PATH_COLOR)

		button_color = BUTTON_HOVER_COLOR if button.collidepoint(pygame.mouse.get_pos()) else BUTTON_COLOR
		pygame.draw.rect(screen, button_color, button, border_radius=7)
		button_text = "Show undistorted path" if show_distorted else "Show distorted path"
		text_surface = button_font.render(button_text, True, BUTTON_TEXT_COLOR)
		screen.blit(text_surface, text_surface.get_rect(center=button.center))

		pygame.display.flip()
		clock.tick(60)

	pygame.quit()


if __name__ == "__main__":
	main()
