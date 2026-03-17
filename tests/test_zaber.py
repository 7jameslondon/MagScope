import argparse

PORT = "COM9"

ONE_REV_DEG = 360.0
TARGET_VEL_DEG_PER_S = 360.0          # 1 rev/s
TARGET_ACCEL_DEG_PER_S2 = 5000.0      # high enough to avoid long ramp times


def run_motion():
    from zaber_motion.ascii import Connection
    from zaber_motion import Units

    with Connection.open_serial_port(PORT) as connection:
        dev = connection.detect_devices()[0]
        axis = dev.get_axis(1)

        # Optional but often good practice:
        # axis.home()

        # Set axis defaults used when velocity/accel not explicitly provided
        axis.settings.set("maxspeed", TARGET_VEL_DEG_PER_S, Units.ANGULAR_VELOCITY_DEGREES_PER_SECOND)
        axis.settings.set("accel", TARGET_ACCEL_DEG_PER_S2, Units.ANGULAR_ACCELERATION_DEGREES_PER_SECOND_SQUARED)

        # Command exactly 1 revolution
        axis.move_relative(ONE_REV_DEG, Units.ANGLE_DEGREES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual Zaber rotary movement utility.")
    parser.add_argument(
        "--confirm-move",
        action="store_true",
        help="Required safety flag. Without this flag, no motion command is sent.",
    )
    args = parser.parse_args()
    if not args.confirm_move:
        raise SystemExit("No motion executed. Re-run with --confirm-move to send hardware motion.")
    run_motion()
