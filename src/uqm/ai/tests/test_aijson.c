#include <stdio.h>
#include <string.h>
#include "aijson.h"

static int failures = 0;

static void
check (const char *what, int ok)
{
	printf ("%-52s %s\n", what, ok ? "ok" : "FAIL");
	if (!ok)
		++failures;
}

int
main (void)
{
	char buf[1024];
	AiJsonWriter w;
	AiJsonObject o;
	const char *s;
	int n;

	/* writer: nested request shape */
	AiJson_InitWriter (&w, buf, sizeof buf);
	AiJson_BeginObject (&w);
	AiJson_WriteString (&w, "type", "converse");
	AiJson_WriteInt (&w, "id", 7);
	AiJson_BeginArray (&w, "actions");
	AiJson_WriteRawElement (&w);
	AiJson_BeginObject (&w);
	AiJson_WriteString (&w, "id", "join_us");
	AiJson_WriteBool (&w, "terminal", TRUE);
	AiJson_EndObject (&w);
	AiJson_EndArray (&w);
	AiJson_EndObject (&w);
	check ("writer produces expected JSON",
			strcmp (buf,
			"{\"type\":\"converse\",\"id\":7,"
			"\"actions\":[{\"id\":\"join_us\",\"terminal\":true}]}") == 0);
	check ("writer reports ok", AiJson_WriterOk (&w));

	/* writer: escaping - a raw newline would split the NDJSON line */
	AiJson_InitWriter (&w, buf, sizeof buf);
	AiJson_BeginObject (&w);
	AiJson_WriteString (&w, "t", "a\"b\\c\nd\te");
	AiJson_EndObject (&w);
	check ("writer escapes quote/backslash/newline/tab",
			strcmp (buf, "{\"t\":\"a\\\"b\\\\c\\nd\\te\"}") == 0);

	/* writer: overflow is detected, not silently truncated */
	{
		char small[16];

		AiJson_InitWriter (&w, small, sizeof small);
		AiJson_BeginObject (&w);
		AiJson_WriteString (&w, "keyname", "a very long value indeed");
		AiJson_EndObject (&w);
		check ("writer flags overflow", !AiJson_WriterOk (&w));
	}

	/* reader: flat response */
	check ("parses flat object", AiJson_Parse (
			"{\"type\":\"converse\",\"id\":7,\"spoken_text\":\"Leave Pluto?!\","
			"\"action\":\"join_us\",\"remember\":null,\"audio_path\":null}", &o));
	s = AiJson_GetString (&o, "spoken_text");
	check ("reads string", s && strcmp (s, "Leave Pluto?!") == 0);
	check ("reads int", AiJson_GetInt (&o, "id", &n) && n == 7);
	check ("detects null", AiJson_IsNull (&o, "remember"));
	check ("absent field is not null", !AiJson_IsNull (&o, "nope"));
	check ("absent string returns NULL", AiJson_GetString (&o, "nope") == NULL);

	/* reader: escapes decoded */
	check ("parses escapes", AiJson_Parse ("{\"t\":\"a\\\"b\\nc\\u0041\"}", &o));
	s = AiJson_GetString (&o, "t");
	check ("decodes escapes", s && strcmp (s, "a\"b\ncA") == 0);

	/* reader: unknown nested fields are skipped, not fatal */
	check ("skips nested object", AiJson_Parse (
			"{\"a\":1,\"extra\":{\"x\":[1,2,{\"y\":3}]},\"b\":\"z\"}", &o));
	s = AiJson_GetString (&o, "b");
	check ("continues past nesting", s && strcmp (s, "z") == 0);

	/* reader: malformed input rejected */
	check ("rejects non-object", !AiJson_Parse ("[1,2,3]", &o));
	check ("rejects truncated", !AiJson_Parse ("{\"a\":", &o));
	check ("rejects unterminated string", !AiJson_Parse ("{\"a\":\"oops}", &o));
	check ("rejects garbage", !AiJson_Parse ("not json", &o));
	check ("rejects NULL", !AiJson_Parse (NULL, &o));

	/* reader: type confusion returns nothing rather than guessing */
	check ("parses typed", AiJson_Parse ("{\"n\":5,\"s\":\"x\"}", &o));
	check ("int field is not a string", AiJson_GetString (&o, "n") == NULL);
	check ("string field is not an int", !AiJson_GetInt (&o, "s", &n));

	printf ("\n%s\n", failures ? "FAILURES" : "all passed");
	return failures != 0;
}
