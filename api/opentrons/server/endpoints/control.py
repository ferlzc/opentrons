import os
import json
import logging
from time import sleep
from aiohttp import web
from threading import Thread
from opentrons import robot, instruments
from opentrons.instruments import pipette_config
from opentrons.trackers import pose_tracker

log = logging.getLogger(__name__)


async def get_attached_pipettes(request):
    """
    Query robot for model strings on 'left' and 'right' mounts, and return a
    dict with the results keyed by mount

    Example:

    ```
    {
      'left': {
        'model': 'p300_single',
        'mount_axis': 'z',
        'plunger_axis': 'b'
      },
      'right': {
        'model': 'p10_multi',
        'mount_axis': 'a',
        'plunger_axis': 'c'
      }
    }
    ```

    If a pipette is "uncommissioned" (e.g.: does not have a model string
    written to on-board memory), or if no pipette is present, the corresponding
    mount will report `'model': null`
    """
    return web.json_response(robot.get_attached_pipettes())


async def get_engaged_axes(request):
    """
    Query driver for engaged state by axis. Response keys will be axes XYZABC
    and keys will be True for engaged and False for disengaged. Axes must be
    manually disengaged, and are automatically re-engaged whenever a "move" or
    "home" command is called on that axis.

    Response shape example:
        {"x": {"enabled": true}, "y": {"enabled": false}, ...}
    """
    return web.json_response(
        {k.lower(): {'enabled': v}
         for k, v in robot._driver.engaged_axes.items()})


async def disengage_axes(request):
    """
    Disengage axes (turn off power) primarily in order to reduce heat
    consumption.
    :param request: Must contain an "axes" field with a list of axes
        to disengage (["x", "y", "z", "a", "b", "c"])
    :return: message and status code
    """
    data = await request.text()
    axes = json.loads(data).get('axes')
    invalid_axes = [ax for ax in axes if ax.lower() not in 'xyzabc']
    if invalid_axes:
        message = "Invalid axes: {}".format(invalid_axes)
        status = 400
    else:
        robot._driver.disengage_axis("".join(axes))
        message = "Disengaged axes: {}".format(axes)
        status = 200
    return web.json_response({"message": message}, status=status)


async def position_info(request):
    """
    Positions determined experimentally by issuing move commands. Change
    pipette position offsets the mount to the left or right such that a user
    can easily access the pipette mount screws with a screwdriver. Attach tip
    position places either pipette roughly in the front-center of the deck area
    """
    return web.json_response({
        'positions': {
            'change_pipette': {
                'target': 'mount',
                'left': (325, 40, 30),
                'right': (65, 40, 30)
            },
            'attach_tip': {
                'target': 'pipette',
                'point': (200, 90, 150)
            }
        }
    })


def _validate_move_data(data):
    error = False
    message = ''
    target = data.get('target')
    if target not in ['mount', 'pipette']:
        message = "Invalid target key: '{}' (target must be one of " \
                  "'mount' or 'pipette'".format(target)
        error = True
    point = data.get('point')
    if type(point) == list:
        point = tuple(point)
    if type(point) is not tuple:
        message = "Point must be an ordered iterable. Got: {}".format(
            type(point))
        error = True
    if point is not None and len(point) is not 3:
        message = "Point must have 3 values--got {}".format(point)
        error = True
    if target is 'mount' and float(point[2]) < 30:
        message = "Sending a mount to a z position lower than 30 can cause " \
                  "a collision with the deck or reach the end of the Z axis " \
                  "movement screw. Z values for mount movement must be >= 30"
        error = True
    mount = data.get('mount')
    if mount not in ['left', 'right']:
        message = "Mount '{}' not supported, must be 'left' or " \
                  "'right'".format(mount)
        error = True
    if target == 'pipette':
        model = data.get('model')
        if model not in pipette_config.configs.keys():
            message = "Model '{}' not recognized, must be one " \
                      "of {}".format(model, pipette_config.configs.keys())
            error = True
    else:
        model = None
    return target, point, mount, model, message, error


async def move(request):
    """
    Moves the robot to the specified position as provided by the `control.info`
    endpoint response

    Post body must include the following keys:
    - 'target': either 'mount' or 'pipette'
    - 'point': a tuple of 3 floats for x, y, z
    - 'mount': must be 'left' or 'right'

    If 'target' is 'pipette', body must also contain:
    - 'model': must be a valid pipette model (as defined in `pipette_config`)
    """
    req = await request.text()
    data = json.loads(req)

    target, point, mount, model, message, error = _validate_move_data(data)
    if error:
        status = 400
    else:
        status = 200
        if target == 'mount':
            message = _move_mount(mount, point)
        elif target == 'pipette':
            config = pipette_config.load(model)
            pipette = instruments._create_pipette_from_config(
                config=config,
                mount=mount)
            pipette.move_to((robot.deck, point))
            new_position = tuple(
                pose_tracker.absolute(pipette.robot.poses, pipette))
            message = "Move complete. New position: {}".format(new_position)

    return web.json_response({"message": message}, status=status)


def _move_mount(mount, point):
    """
    The carriage moves the mount in the Z axis, and the gantry moves in X and Y

    Mount movements do not have the same protections calculated in to an
    existing `move` command like Pipette does, so the safest thing is to home
    the Z axis, then move in X and Y, then move down to the specified Z height
    """
    carriage = robot._actuators[mount]['carriage']

    # Home both carriages, to prevent collisions and to ensure that the other
    # mount doesn't block the one being moved (mount moves are primarily for
    # changing pipettes, so we don't want the other pipette blocking access)
    robot.poses = carriage.home(robot.poses)
    other_mount = 'left' if mount == 'right' else 'right'
    robot.poses = robot._actuators[other_mount]['carriage'].home(robot.poses)

    robot.gantry.move(
        robot.poses, x=point[0], y=point[1])
    robot.poses = carriage.move(
        robot.poses, z=point[2])

    # These x and y values are hard to interpret because of some internals of
    # pose tracker. It's mostly z that matters for this operation anyway
    x, y, _ = tuple(
        pose_tracker.absolute(
            robot.poses, robot._actuators[mount]['carriage']))
    _, _, z = tuple(
        pose_tracker.absolute(
            robot.poses, robot.gantry))
    new_position = (x, y, z)
    return "Move complete. New position: {}".format(new_position)


async def identify(request):
    Thread(target=lambda: robot.identify(
        int(request.query.get('seconds', '10')))).start()
    return web.json_response({"message": "identifying"})


async def turn_on_rail_lights(request):
    robot.turn_on_rail_lights()
    return web.json_response({"lights": "on"})


async def turn_off_rail_lights(request):
    robot.turn_off_rail_lights()
    return web.json_response({"lights": "off"})


async def restart(request):
    """
    Returns OK, then waits approximately 3 seconds and restarts container
    """
    def wait_and_restart():
        log.info('Restarting server')
        sleep(3)
        os.system('kill 1')
    Thread(target=wait_and_restart).start()
    return web.json_response({"message": "restarting"})
