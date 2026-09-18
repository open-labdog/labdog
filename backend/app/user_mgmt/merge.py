from sqlalchemy.ext.asyncio import AsyncSession

from app.enum_utils import enum_str
from app.merge_utils import OwnedRow, first_wins, load_owned_rows
from app.user_mgmt.models import LinuxGroup, LinuxUser
from app.user_mgmt.schemas import EffectiveLinuxGroupResponse, EffectiveLinuxUserResponse


def _user_response(owned: OwnedRow[LinuxUser]) -> EffectiveLinuxUserResponse:
    rule = owned.row
    return EffectiveLinuxUserResponse(
        username=rule.username,
        uid=rule.uid,
        shell=rule.shell,
        home_dir=rule.home_dir,
        state=enum_str(rule.state),
        comment=rule.comment,
        sudo_rule=rule.sudo_rule,
        authorized_keys=rule.authorized_keys or [],
        supplementary_groups=rule.supplementary_groups or [],
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


def _group_response(owned: OwnedRow[LinuxGroup]) -> EffectiveLinuxGroupResponse:
    rule = owned.row
    return EffectiveLinuxGroupResponse(
        groupname=rule.groupname,
        gid=rule.gid,
        state=enum_str(rule.state),
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


async def get_effective_users(host_id: int, db: AsyncSession) -> list[EffectiveLinuxUserResponse]:
    """Merge group-level LinuxUser rules + host-level overrides.

    Merge key: ``username``. Host override = full replacement; among rows
    at the same level the highest priority wins.
    """
    owned = await load_owned_rows(db, host_id, LinuxUser)
    winners = first_wins(owned, key=lambda r: r.username)
    return sorted((_user_response(o) for o in winners), key=lambda u: u.username)


async def get_effective_groups(host_id: int, db: AsyncSession) -> list[EffectiveLinuxGroupResponse]:
    """Merge group-level LinuxGroup rules + host-level overrides.

    Merge key: ``groupname``. Host override = full replacement; among rows
    at the same level the highest priority wins.
    """
    owned = await load_owned_rows(db, host_id, LinuxGroup)
    winners = first_wins(owned, key=lambda r: r.groupname)
    return sorted((_group_response(o) for o in winners), key=lambda g: g.groupname)
