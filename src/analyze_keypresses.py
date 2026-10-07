"""The original analysis of keypress_times.txt into 0.1 s buckets; calibration.py replaces it."""

keypress_times: list[float] = [
	float(keypress_time) for keypress_time in open("keypress_times.txt").read().splitlines()
]

for i in range(10):
	interval_start = i*0.1
	interval_end = (i+1)*0.1

	within_interval = sum(interval_start <= keypress_time < interval_end for keypress_time in keypress_times)

	percent_within_interval = within_interval / len(keypress_times) * 100

	print(f"Interval {interval_start:.1f}-{interval_end:.1f}: {within_interval} keypresses ({percent_within_interval:.2f}%)")