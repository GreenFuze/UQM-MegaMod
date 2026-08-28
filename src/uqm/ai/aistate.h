/*
 *  What the game tells the sidecar about where the story has got to.
 *
 *  Kept separate from aiconv.c on purpose. aiconv.c is the protocol, and it
 *  includes almost nothing of the game so that the wire format can be reasoned
 *  about (and tested) on its own. This file is the opposite: it is allowed to
 *  know about game state, the clock and CommData, and it hands aiconv.c plain
 *  values with no UQM types in the signatures.
 */

#ifndef UQM_AI_AISTATE_H
#define UQM_AI_AISTATE_H

/* One named game state flag and its current value. */
typedef struct
{
	const char *name;
	unsigned int value;
} AI_STATE_ENTRY;

/* Collects the state worth sending: every named flag that is non-zero, plus
 * the derived SIS_* values.
 *
 * Non-zero only, because absent already means zero on the other side - that is
 * getGameStateUint's own contract for an unset property - so sending the zeros
 * would triple the line for no information. Early in a game this is a few
 * hundred bytes; the full 453 would be about 10 KB against a 32 KB line.
 *
 * Returns how many entries were written. */
int AiState_Collect (AI_STATE_ENTRY *out, int cap);

/* The in-game calendar date, so a character's tenses are right and so a fact
 * that is not true yet can be withheld. */
void AiState_Date (int *day, int *month, int *year);

/* Who the player is talking to, as the dialogue resource name - for instance
 * "comm.starbase.dialogue".
 *
 * This is LOCDATA.ConversationPhrasesRes, which is already a distinct string
 * per character and is already correct across the two forks init_race
 * performs: commander/starbase on STARBASE_AVAILABLE, and spathi/safeones on
 * the homeworld bit. So identity needed nothing added to any of the 27
 * conversation files.
 *
 * NULL outside a conversation. */
const char *AiState_CharacterId (void);

#endif /* UQM_AI_AISTATE_H */
