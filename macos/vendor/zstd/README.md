# Zstandard decoder

Zstandard v1.5.7 from https://github.com/facebook/zstd/releases/tag/v1.5.7.
The BSD license is preserved in `LICENSE` and in the amalgamated source.
`zstddeclib.c` is the unmodified official decompressor amalgamation generated with:

```
cd zstd-1.5.7/build/single_file_libs
python3 combine.py -r ../../lib -x legacy/zstd_legacy.h -o zstddeclib.c zstddeclib-in.c
```

`macos/native/RolloutZstd.c` uses its streaming API with a bounded window and
decoded size. `RolloutFile.swift` atomically caches verified decompressed bytes;
the existing indexer, request metadata recovery and journey paging keep their
original source paths, record identities, checkpoints and byte offsets.
Windows uses the pure Go Zstandard implementation in klauspost/compress v1.20.3.
