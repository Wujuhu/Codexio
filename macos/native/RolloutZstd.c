#include <errno.h>
#include <stdint.h>
#include <unistd.h>
#include "../vendor/zstd/zstddeclib.c"

/* The official Zstandard streaming decoder; no external command or runtime. */
int codexio_zstd_decode(int source, int destination, uint64_t limit) {
    ZSTD_DStream *stream = ZSTD_createDStream();
    size_t capacity = ZSTD_DStreamInSize(), outCapacity = ZSTD_DStreamOutSize();
    void *input = malloc(capacity), *output = malloc(outCapacity);
    int error = 0, received = 0;
    uint64_t written = 0;
    size_t remaining = 1;
    if (!stream || !input || !output) { error = ENOMEM; goto done; }
    if (ZSTD_isError(ZSTD_DCtx_setParameter(stream, ZSTD_d_windowLogMax, 27))) { error = EINVAL; goto done; }
    for (;;) {
        ssize_t size;
        do { size = read(source, input, capacity); } while (size < 0 && errno == EINTR);
        if (size < 0) { error = errno; break; }
        if (size == 0) { if (!received || remaining != 0) error = EILSEQ; break; }
        received = 1;
        ZSTD_inBuffer in = {input, (size_t)size, 0};
        while (in.pos < in.size) {
            ZSTD_outBuffer out = {output, outCapacity, 0};
            remaining = ZSTD_decompressStream(stream, &out, &in);
            if (ZSTD_isError(remaining)) { error = EILSEQ; goto done; }
            if (out.pos > limit - written) { error = EFBIG; goto done; }
            size_t offset = 0;
            while (offset < out.pos) {
                ssize_t count = write(destination, (char *)output + offset, out.pos - offset);
                if (count < 0 && errno == EINTR) continue;
                if (count <= 0) { error = count < 0 ? errno : EIO; goto done; }
                offset += (size_t)count;
            }
            written += out.pos;
        }
    }
done:
    ZSTD_freeDStream(stream); free(input); free(output);
    return error;
}
