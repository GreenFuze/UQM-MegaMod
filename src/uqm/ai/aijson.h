/*
 *  Minimal JSON support for the AI sidecar protocol.
 *
 *  Deliberately not a general JSON library. The game writes structured
 *  requests and reads flat responses, so the writer is a string builder and
 *  the reader accepts only a flat object of scalar values. Anything else --
 *  nesting, arrays, malformed input -- is rejected rather than interpreted.
 *
 *  The sidecar is untrusted by design (docs/ai-architecture.md), so the
 *  parsing direction is kept as small and auditable as possible.
 */

#ifndef UQM_AI_AIJSON_H
#define UQM_AI_AIJSON_H

#include <stddef.h>
#include "libs/compiler.h"

#define AIJSON_MAX_FIELDS   16
#define AIJSON_MAX_KEY      32
#define AIJSON_MAX_VALUE    2048

/* ---- writing ---------------------------------------------------------- */

typedef struct
{
	char *buf;
	size_t cap;
	size_t len;
	BOOLEAN overflow;  /* set once the buffer fills; the result is unusable */
	BOOLEAN needComma; /* whether the next member must be preceded by ',' */
} AiJsonWriter;

void AiJson_InitWriter (AiJsonWriter *w, char *buf, size_t cap);
void AiJson_BeginObject (AiJsonWriter *w);
void AiJson_EndObject (AiJsonWriter *w);
void AiJson_BeginArray (AiJsonWriter *w, const char *key);
void AiJson_EndArray (AiJsonWriter *w);
void AiJson_WriteString (AiJsonWriter *w, const char *key, const char *value);
void AiJson_WriteInt (AiJsonWriter *w, const char *key, int value);
void AiJson_WriteBool (AiJsonWriter *w, const char *key, BOOLEAN value);
void AiJson_WriteRawElement (AiJsonWriter *w);
BOOLEAN AiJson_WriterOk (const AiJsonWriter *w);

/* ---- reading ---------------------------------------------------------- */

typedef enum
{
	AIJSON_NULL,
	AIJSON_STRING,
	AIJSON_NUMBER,
	AIJSON_BOOL
} AiJsonType;

typedef struct
{
	char key[AIJSON_MAX_KEY];
	AiJsonType type;
	char str[AIJSON_MAX_VALUE]; /* decoded string, or "" */
	int num;                    /* number, or bool as 0/1 */
} AiJsonField;

typedef struct
{
	AiJsonField fields[AIJSON_MAX_FIELDS];
	int count;
} AiJsonObject;

/* Parses one flat JSON object. Returns FALSE on anything unexpected. */
BOOLEAN AiJson_Parse (const char *text, AiJsonObject *out);

/* Lookups. Return NULL/default when the field is absent or the wrong type. */
const char *AiJson_GetString (const AiJsonObject *obj, const char *key);
BOOLEAN AiJson_GetInt (const AiJsonObject *obj, const char *key, int *out);
BOOLEAN AiJson_IsNull (const AiJsonObject *obj, const char *key);

#endif /* UQM_AI_AIJSON_H */
