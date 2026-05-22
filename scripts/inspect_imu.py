import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from insv_tools.accelerometer import read_accelerometer_timeseries


DEFAULT_IMU_FILE = "../sample_insvs_x4/VID_20260427_162323_00_551.insv"


def main(imu_file: str = DEFAULT_IMU_FILE) -> None:
    imu = read_accelerometer_timeseries(imu_file)
    print(f"sample count: {imu.sample_count}")
    print(f"sample rate: {imu.sample_rate_hz}")
    print(f"first relative second: {imu.relative_seconds[0]}")
    print(f"first acceleration: {imu.acceleration[0]}")       # (accel_x, accel_y, accel_z)
    print(f"first angular velocity: {imu.angular_velocity[0]}")   # (gyro_x, gyro_y, gyro_z)
    print(f"total duration in seconds: {imu.duration_seconds} seconds")
    total_duration_minutes = imu.duration_seconds / 60
    print(f"total duration in minutes: {total_duration_minutes} minutes")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Test the insv_accelerometer module.')
    parser.add_argument(
        'imu_file',
        type=str,
        default=DEFAULT_IMU_FILE,
        nargs='?',
        help='Path to the .insv file containing the accelerometer data.',
    )
    args = parser.parse_args()
    main(args.imu_file)
