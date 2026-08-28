/*
 *  Game state for the AI sidecar. See aistate.h.
 */

#include "aistate.h"

#include "../build.h"
#include "../clock.h"
#include "../commglue.h"
#include "../globdata.h"
#include "../save.h"


/* Values the game computes rather than stores.
 *
 * Several characters gate what they say on the state of the ship and fleet
 * rather than on a saved flag. The Commander's own briefing is the clearest
 * case: starbas.c decides between "build up your flagship", "go make allies"
 * and "attack the Sa-Matra" using fleet strength and an ally count, none of
 * which appear in gameStateBitMap. A knowledge model built only on saved flags
 * could not reproduce what he actually says.
 *
 * Namespaced SIS_ after the game's own name for the flagship, so these can
 * never collide with a real flag name. */
static int
collectDerived (AI_STATE_ENTRY *out, int cap, int count)
{
	COUNT i;
	unsigned int allies = 0;

	for (i = 0; i < NUM_AVAILABLE_RACES; ++i)
	{
		if (i != HUMAN_SHIP && CheckAlliance (i) == GOOD_GUY)
			++allies;
	}

	if (count < cap)
	{
		out[count].name = "SIS_ALLY_COUNT";
		out[count].value = allies;
		++count;
	}
	if (count < cap)
	{
		out[count].name = "SIS_FLEET_STRENGTH";
		out[count].value = (unsigned int)CalculateEscortsWorth ();
		++count;
	}
	if (count < cap)
	{
		out[count].name = "SIS_RESOURCE_UNITS";
		out[count].value = (unsigned int)GLOBAL_SIS (ResUnits);
		++count;
	}
	if (count < cap)
	{
		out[count].name = "SIS_FUEL";
		out[count].value = (unsigned int)GLOBAL_SIS (FuelOnBoard);
		++count;
	}
	if (count < cap)
	{
		out[count].name = "SIS_CREW";
		out[count].value = (unsigned int)GLOBAL_SIS (CrewEnlisted);
		++count;
	}
	if (count < cap)
	{
		out[count].name = "SIS_LANDERS";
		out[count].value = (unsigned int)GLOBAL_SIS (NumLanders);
		++count;
	}

	return count;
}

int
AiState_Collect (AI_STATE_ENTRY *out, int cap)
{
	const GameStateBitMap *entry;
	int count = 0;

	if (out == NULL || cap <= 0)
		return 0;

	/* The same walk serialiseGameState does, and for the same reason: the
	 * table is the authoritative list of what the game has. */
	for (entry = gameStateBitMap; entry->name != NULL; ++entry)
	{
		DWORD value;

		if (entry->numBits == 0)
			continue;               /* revision marker, not a flag */

		value = getGameStateUint (entry->name);
		if (value == 0)
			continue;               /* absent means zero on the other side */

		if (count >= cap)
			break;

		out[count].name = entry->name;
		out[count].value = (unsigned int)value;
		++count;
	}

	return collectDerived (out, cap, count);
}

void
AiState_Date (int *day, int *month, int *year)
{
	if (day != NULL)
		*day = (int)GLOBAL (GameClock.day_index);
	if (month != NULL)
		*month = (int)GLOBAL (GameClock.month_index);
	if (year != NULL)
		*year = (int)GLOBAL (GameClock.year_index);
}

const char *
AiState_CharacterId (void)
{
	return CommData.ConversationPhrasesRes;
}
