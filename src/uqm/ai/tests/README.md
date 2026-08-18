# AI module unit tests

Standalone tests, not part of the game build. Compile and run directly:

```bash
gcc -std=gnu99 -Wall -Wextra -I src -I src/uqm/ai \
    -o test_aijson src/uqm/ai/tests/test_aijson.c src/uqm/ai/aijson.c
./test_aijson
```

On Windows use the MINGW64 toolchain (`C:\msys64\mingw64\bin\gcc`); it needs its own
`bin` on PATH or it exits silently with no diagnostics.
