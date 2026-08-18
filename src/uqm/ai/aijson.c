/*
 *  Minimal JSON writer/reader for the AI sidecar protocol. See aijson.h.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "aijson.h"

/* ---- writing ---------------------------------------------------------- */

static void
appendChar (AiJsonWriter *w, char c)
{
	/* Leave room for the NUL so the buffer is always a valid C string. */
	if (w->overflow || w->len + 1 >= w->cap)
	{
		w->overflow = TRUE;
		return;
	}
	w->buf[w->len++] = c;
	w->buf[w->len] = '\0';
}

static void
appendRaw (AiJsonWriter *w, const char *s)
{
	for (; *s != '\0'; ++s)
		appendChar (w, *s);
}

/* Escapes per RFC 8259. Control characters must not reach the wire, since a
 * raw newline would split one message into two NDJSON lines. */
static void
appendQuoted (AiJsonWriter *w, const char *s)
{
	appendChar (w, '"');
	if (s != NULL)
	{
		for (; *s != '\0'; ++s)
		{
			unsigned char c = (unsigned char)*s;
			switch (c)
			{
				case '"':
					appendRaw (w, "\\\"");
					break;
				case '\\':
					appendRaw (w, "\\\\");
					break;
				case '\n':
					appendRaw (w, "\\n");
					break;
				case '\r':
					appendRaw (w, "\\r");
					break;
				case '\t':
					appendRaw (w, "\\t");
					break;
				default:
					if (c < 0x20)
					{
						char esc[8];
						sprintf (esc, "\\u%04x", (unsigned)c);
						appendRaw (w, esc);
					}
					else
						appendChar (w, (char)c);
					break;
			}
		}
	}
	appendChar (w, '"');
}

static void
appendSeparator (AiJsonWriter *w)
{
	if (w->needComma)
		appendChar (w, ',');
	w->needComma = TRUE;
}

static void
appendKey (AiJsonWriter *w, const char *key)
{
	appendSeparator (w);
	if (key != NULL)
	{
		appendQuoted (w, key);
		appendChar (w, ':');
	}
}

void
AiJson_InitWriter (AiJsonWriter *w, char *buf, size_t cap)
{
	w->buf = buf;
	w->cap = cap;
	w->len = 0;
	w->overflow = FALSE;
	w->needComma = FALSE;
	if (cap > 0)
		buf[0] = '\0';
}

void
AiJson_BeginObject (AiJsonWriter *w)
{
	appendSeparator (w);
	appendChar (w, '{');
	w->needComma = FALSE;
}

void
AiJson_EndObject (AiJsonWriter *w)
{
	appendChar (w, '}');
	w->needComma = TRUE;
}

void
AiJson_BeginArray (AiJsonWriter *w, const char *key)
{
	appendKey (w, key);
	appendChar (w, '[');
	w->needComma = FALSE;
}

void
AiJson_EndArray (AiJsonWriter *w)
{
	appendChar (w, ']');
	w->needComma = TRUE;
}

void
AiJson_WriteString (AiJsonWriter *w, const char *key, const char *value)
{
	appendKey (w, key);
	appendQuoted (w, value);
}

void
AiJson_WriteInt (AiJsonWriter *w, const char *key, int value)
{
	char num[16];
	appendKey (w, key);
	sprintf (num, "%d", value);
	appendRaw (w, num);
}

void
AiJson_WriteBool (AiJsonWriter *w, const char *key, BOOLEAN value)
{
	appendKey (w, key);
	appendRaw (w, value ? "true" : "false");
}

void
AiJson_WriteRawElement (AiJsonWriter *w)
{
	/* Used before writing an object inside an array, where no key applies. */
	appendSeparator (w);
	w->needComma = FALSE;
}

BOOLEAN
AiJson_WriterOk (const AiJsonWriter *w)
{
	return (BOOLEAN)!w->overflow;
}

/* ---- reading ---------------------------------------------------------- */

static const char *
skipSpace (const char *p)
{
	while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
		++p;
	return p;
}

/* Reads a quoted string into dst. Returns NULL on malformed input.
 * \u escapes are decoded only for the ASCII range; anything higher becomes
 * '?' rather than attempting UTF-16 surrogate handling, which this protocol
 * has no need for. */
static const char *
parseString (const char *p, char *dst, size_t cap)
{
	size_t n = 0;

	if (*p != '"')
		return NULL;
	++p;

	while (*p != '"')
	{
		char c = *p;

		if (c == '\0')
			return NULL; /* unterminated */

		if (c == '\\')
		{
			++p;
			switch (*p)
			{
				case '"':
					c = '"';
					break;
				case '\\':
					c = '\\';
					break;
				case '/':
					c = '/';
					break;
				case 'n':
					c = '\n';
					break;
				case 'r':
					c = '\r';
					break;
				case 't':
					c = '\t';
					break;
				case 'b':
					c = '\b';
					break;
				case 'f':
					c = '\f';
					break;
				case 'u':
				{
					char hex[5];
					long v;
					int i;

					for (i = 0; i < 4; ++i)
					{
						if (p[1 + i] == '\0')
							return NULL;
						hex[i] = p[1 + i];
					}
					hex[4] = '\0';
					v = strtol (hex, NULL, 16);
					c = (v >= 0x20 && v < 0x7f) ? (char)v : '?';
					p += 4;
					break;
				}
				default:
					return NULL; /* unknown escape */
			}
		}

		if (n + 1 < cap)
			dst[n++] = c;
		++p;
	}

	dst[n] = '\0';
	return p + 1; /* consume closing quote */
}

/* Skips one value without recording it, so a response carrying an unexpected
 * nested object or array is ignored field-wise rather than failing the whole
 * message. Depth is bounded to refuse pathological input. */
static const char *
skipValue (const char *p, int depth)
{
	if (depth > 8)
		return NULL;

	p = skipSpace (p);

	if (*p == '{' || *p == '[')
	{
		char open = *p;
		char close = (open == '{') ? '}' : ']';

		++p;
		for (;;)
		{
			p = skipSpace (p);
			if (*p == '\0')
				return NULL;
			if (*p == close)
				return p + 1;
			if (*p == ',' || *p == ':')
			{
				++p;
				continue;
			}
			p = skipValue (p, depth + 1);
			if (p == NULL)
				return NULL;
		}
	}

	if (*p == '"')
	{
		/* Walk to the terminator, honouring escapes. */
		const char *q = p + 1;

		while (*q != '"')
		{
			if (*q == '\0')
				return NULL;
			if (*q == '\\' && q[1] != '\0')
				++q;
			++q;
		}
		return q + 1;
	}

	while (*p != '\0' && *p != ',' && *p != '}' && *p != ']')
		++p;
	return p;
}

BOOLEAN
AiJson_Parse (const char *text, AiJsonObject *out)
{
	const char *p;

	out->count = 0;
	if (text == NULL)
		return FALSE;

	p = skipSpace (text);
	if (*p != '{')
		return FALSE;
	++p;

	for (;;)
	{
		AiJsonField *f;
		char key[AIJSON_MAX_KEY];

		p = skipSpace (p);
		if (*p == '}')
			return TRUE;
		if (*p == ',')
		{
			++p;
			continue;
		}
		if (*p == '\0')
			return FALSE;

		p = parseString (p, key, sizeof key);
		if (p == NULL)
			return FALSE;

		p = skipSpace (p);
		if (*p != ':')
			return FALSE;
		p = skipSpace (p + 1);

		/* Values beyond our capacity, and non-scalar values, are skipped
		 * rather than treated as errors: the sidecar may legitimately add
		 * fields this build does not know about. */
		if (out->count >= AIJSON_MAX_FIELDS || *p == '{' || *p == '[')
		{
			p = skipValue (p, 0);
			if (p == NULL)
				return FALSE;
			continue;
		}

		f = &out->fields[out->count];
		memset (f, 0, sizeof (*f));
		strncpy (f->key, key, AIJSON_MAX_KEY - 1);

		if (*p == '"')
		{
			f->type = AIJSON_STRING;
			p = parseString (p, f->str, sizeof f->str);
			if (p == NULL)
				return FALSE;
		}
		else if (strncmp (p, "null", 4) == 0)
		{
			f->type = AIJSON_NULL;
			p += 4;
		}
		else if (strncmp (p, "true", 4) == 0)
		{
			f->type = AIJSON_BOOL;
			f->num = 1;
			p += 4;
		}
		else if (strncmp (p, "false", 5) == 0)
		{
			f->type = AIJSON_BOOL;
			f->num = 0;
			p += 5;
		}
		else
		{
			char *end;
			long v = strtol (p, &end, 10);

			if (end == p)
				return FALSE;
			f->type = AIJSON_NUMBER;
			f->num = (int)v;
			p = end;
		}

		++out->count;
	}
}

static const AiJsonField *
findField (const AiJsonObject *obj, const char *key)
{
	int i;

	for (i = 0; i < obj->count; ++i)
	{
		if (strcmp (obj->fields[i].key, key) == 0)
			return &obj->fields[i];
	}
	return NULL;
}

const char *
AiJson_GetString (const AiJsonObject *obj, const char *key)
{
	const AiJsonField *f = findField (obj, key);

	if (f == NULL || f->type != AIJSON_STRING)
		return NULL;
	return f->str;
}

BOOLEAN
AiJson_GetInt (const AiJsonObject *obj, const char *key, int *out)
{
	const AiJsonField *f = findField (obj, key);

	if (f == NULL || (f->type != AIJSON_NUMBER && f->type != AIJSON_BOOL))
		return FALSE;
	*out = f->num;
	return TRUE;
}

BOOLEAN
AiJson_IsNull (const AiJsonObject *obj, const char *key)
{
	const AiJsonField *f = findField (obj, key);

	return (BOOLEAN)(f != NULL && f->type == AIJSON_NULL);
}
