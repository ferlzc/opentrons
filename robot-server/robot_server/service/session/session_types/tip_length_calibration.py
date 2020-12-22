from typing import cast, Awaitable
from opentrons.types import Mount
from robot_server.robot.calibration.tip_length.user_flow import \
    TipCalibrationUserFlow
from robot_server.robot.calibration.models import \
    SessionCreateParams
from robot_server.robot.calibration.tip_length.models import \
    TipCalibrationSessionStatus
from robot_server.service.session.errors import (SessionCreationException,
                                                 CommandExecutionException)
from robot_server.service.session.command_execution import \
     CallableExecutor, Command, CompletedCommand, CommandQueue, CommandExecutor
from opentrons.protocol_api import labware

from .base_session import BaseSession, SessionMetaData
from ..configuration import SessionConfiguration
from ..models.session import (SessionType,
                              TipLengthCalibrationResponseAttributes)
from ..errors import UnsupportedFeature


class TipLengthCalibrationCommandExecutor(CallableExecutor):

    async def execute(self, command: Command) -> CompletedCommand:
        try:
            return await super().execute(command)
        except AssertionError as e:
            raise CommandExecutionException(str(e))


class TipLengthCalibration(BaseSession):
    def __init__(self, configuration: SessionConfiguration,
                 instance_meta: SessionMetaData,
                 tip_cal_user_flow: TipCalibrationUserFlow,
                 shutdown_handler: Awaitable[None] = None
                 ):
        super().__init__(configuration, instance_meta)
        self._tip_cal_user_flow = tip_cal_user_flow
        self._command_executor = TipLengthCalibrationCommandExecutor(
            self._tip_cal_user_flow.handle_command
        )
        self._shutdown_coroutine = shutdown_handler

    @classmethod
    async def create(cls, configuration: SessionConfiguration,
                     instance_meta: SessionMetaData) -> 'BaseSession':
        assert isinstance(instance_meta.create_params, SessionCreateParams)
        has_calibration_block = instance_meta.create_params.hasCalibrationBlock
        mount = instance_meta.create_params.mount
        tip_rack_def = instance_meta.create_params.tipRackDefinition
        if tip_rack_def:
            verified_definition = labware.verify_definition(tip_rack_def)
        else:
            raise SessionCreationException('No tiprack def provided')
        # if lights are on already it's because the user clicked the button,
        # so a) we don't need to turn them on now and b) we shouldn't turn them
        # off after
        session_controls_lights =\
            not configuration.hardware.get_lights()['rails']
        await configuration.hardware.cache_instruments()
        await configuration.hardware.home()
        try:
            tip_cal_user_flow = TipCalibrationUserFlow(
                    hardware=configuration.hardware,
                    mount=Mount[mount.upper()],
                    has_calibration_block=has_calibration_block,
                    tip_rack=verified_definition)
        except AssertionError as e:
            raise SessionCreationException(str(e))

        if session_controls_lights:
            await configuration.hardware.set_lights(rails=True)
            shutdown_handler = configuration.hardware.set_lights(rails=False)
        else:
            shutdown_handler = None

        return cls(configuration=configuration,
                   instance_meta=instance_meta,
                   tip_cal_user_flow=tip_cal_user_flow,
                   shutdown_handler=shutdown_handler)

    @property
    def command_executor(self) -> CommandExecutor:
        return self._command_executor

    @property
    def command_queue(self) -> CommandQueue:
        raise UnsupportedFeature()

    @property
    def session_type(self) -> SessionType:
        return SessionType.tip_length_calibration

    def get_response_model(self) -> TipLengthCalibrationResponseAttributes:
        return TipLengthCalibrationResponseAttributes(
            id=self.meta.identifier,
            details=self._get_response_details(),
            createdAt=self.meta.created_at,
            createParams=cast(SessionCreateParams, self.meta.create_params)
        )

    def _get_response_details(self) -> TipCalibrationSessionStatus:
        return TipCalibrationSessionStatus(
            instrument=self._tip_cal_user_flow.get_pipette(),
            currentStep=self._tip_cal_user_flow.current_state,
            labware=self._tip_cal_user_flow.get_required_labware(),
            supportedCommands=self._tip_cal_user_flow.get_supported_commands()
        )

    async def clean_up(self):
        if self._shutdown_coroutine:
            await self._shutdown_coroutine
