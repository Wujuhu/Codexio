#include <zlib.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>

/* Standard zlib streams, shared with Go's compress/zlib. Only one request is
   materialized at a time; no whole-history plaintext archive is created. */
int codexio_archive_compress(const void *input, size_t size, void **output, size_t *count) {
    uLongf capacity = compressBound((uLong)size);
    *output = malloc(capacity ? capacity : 1);
    if (!*output) return ENOMEM;
    int result = compress2(*output, &capacity, input, (uLong)size, Z_BEST_SPEED);
    if (result != Z_OK) { free(*output); *output = NULL; return EILSEQ; }
    *count = capacity;
    return 0;
}
int codexio_archive_decompress(const void *input, size_t size, void **output, size_t expected) {
    *output = malloc(expected ? expected : 1);
    if (!*output) return ENOMEM;
    uLongf count = (uLongf)expected;
    int result = uncompress(*output, &count, input, (uLong)size);
    if (result != Z_OK || count != expected) { free(*output); *output = NULL; return EILSEQ; }
    return 0;
}
void codexio_archive_free(void *pointer) { free(pointer); }
