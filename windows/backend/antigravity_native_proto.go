package backend

import (
	"errors"
	"io"
	"math"
)

type nativeProtoField struct {
	number uint64
	wire   uint64
	value  uint64
	offset int64
	length int64
}

// Unknown length-delimited fields are skipped by offset. In particular,
// ChatModel's system/message prompts are never decoded or copied into Go.
func nativeProtoFields(reader io.ReaderAt, start, end int64, visit func(nativeProtoField) error) error {
	for start < end {
		tag, next, err := nativeProtoUint(reader, start, end)
		if err != nil || tag>>3 == 0 {
			return errors.New("原生计时 protobuf 标签无效")
		}
		field := nativeProtoField{number: tag >> 3, wire: tag & 7}
		start = next
		switch field.wire {
		case 0:
			field.value, start, err = nativeProtoUint(reader, start, end)
		case 1:
			start += 8
		case 2:
			var size uint64
			size, start, err = nativeProtoUint(reader, start, end)
			if size > math.MaxInt64 || int64(size) > end-start {
				return errors.New("原生计时 protobuf 长度无效")
			}
			field.offset, field.length = start, int64(size)
			start += int64(size)
		case 5:
			start += 4
		default:
			return errors.New("原生计时 protobuf 类型无效")
		}
		if err != nil {
			return err
		}
		if start > end {
			return io.ErrUnexpectedEOF
		}
		if err = visit(field); err != nil {
			return err
		}
	}
	return nil
}

func nativeProtoUint(reader io.ReaderAt, offset, end int64) (uint64, int64, error) {
	var value uint64
	var single [1]byte
	for shift := uint(0); shift < 70; shift += 7 {
		if offset >= end {
			return 0, offset, io.ErrUnexpectedEOF
		}
		if _, err := reader.ReadAt(single[:], offset); err != nil {
			return 0, offset, err
		}
		offset++
		b := single[0]
		if shift == 63 && b > 1 {
			return 0, offset, errors.New("原生计时 protobuf 整数溢出")
		}
		value |= uint64(b&127) << shift
		if b < 128 {
			return value, offset, nil
		}
	}
	return 0, offset, errors.New("原生计时 protobuf 整数无效")
}

type nativeDuration struct {
	seconds int64
	nanos   int64
	known   bool
}

func (d nativeDuration) milliseconds() float64 { return float64(d.seconds)*1000 + float64(d.nanos)/1e6 }

func nativeProtoTime(reader io.ReaderAt, offset, length int64, duration bool) (nativeDuration, error) {
	value := nativeDuration{known: true}
	seen := map[uint64]uint64{}
	err := nativeProtoFields(reader, offset, offset+length, func(field nativeProtoField) error {
		if field.number != 1 && field.number != 2 {
			return nil
		}
		if field.wire != 0 {
			return errors.New("原生计时时间字段无效")
		}
		if previous, exists := seen[field.number]; exists && previous != field.value {
			return errors.New("原生计时时间字段冲突")
		}
		seen[field.number] = field.value
		if field.number == 1 {
			value.seconds = int64(field.value)
		} else {
			value.nanos = int64(field.value)
		}
		return nil
	})
	maximum := int64(253402300799)
	if duration {
		maximum = 315576000000
	}
	if value.seconds < 0 || value.seconds > maximum || value.nanos < 0 || value.nanos >= 1e9 {
		value.known = false
	}
	return value, err
}

type nativeGeneration struct {
	steps                  map[int64]bool
	chatOffset, chatLength int64
	chatKnown              bool
	input, cached, output  uint64
	usageKnown             bool
	ttft, stream           nativeDuration
}

func nativeGenerationHeader(blob *sourceBlob) (nativeGeneration, error) {
	value := nativeGeneration{steps: map[int64]bool{}}
	err := nativeProtoFields(blob, 0, blob.size, func(field nativeProtoField) error {
		if field.number == 1 && field.wire == 2 {
			if value.chatKnown {
				return errors.New("原生计时生成记录冲突")
			}
			value.chatOffset, value.chatLength, value.chatKnown = field.offset, field.length, true
		}
		if field.number != 2 {
			return nil
		}
		if field.wire == 0 {
			if field.value <= math.MaxInt64 {
				value.steps[int64(field.value)] = true
			}
		} else if field.wire == 2 {
			for at := field.offset; at < field.offset+field.length; {
				n, next, err := nativeProtoUint(blob, at, field.offset+field.length)
				if err != nil {
					return err
				}
				if n > math.MaxInt64 {
					return errors.New("原生计时步骤编号无效")
				}
				value.steps[int64(n)] = true
				at = next
			}
		}
		return nil
	})
	return value, err
}

func (g *nativeGeneration) readTiming(blob *sourceBlob) error {
	seenDurations := map[uint64]nativeDuration{}
	return nativeProtoFields(blob, g.chatOffset, g.chatOffset+g.chatLength, func(field nativeProtoField) error {
		if field.wire != 2 {
			return nil
		}
		switch field.number {
		case 4:
			if g.usageKnown {
				return errors.New("原生计量字段冲突")
			}
			g.usageKnown = true
			seenCounters := map[uint64]uint64{}
			return nativeProtoFields(blob, field.offset, field.offset+field.length, func(counter nativeProtoField) error {
				if counter.number != 2 && counter.number != 3 && counter.number != 5 {
					return nil
				}
				if counter.wire != 0 {
					return errors.New("原生计量字段类型无效")
				}
				if previous, exists := seenCounters[counter.number]; exists && previous != counter.value {
					return errors.New("原生计量字段冲突")
				}
				seenCounters[counter.number] = counter.value
				switch counter.number {
				case 2:
					g.input = counter.value
				case 3:
					g.output = counter.value
				case 5:
					g.cached = counter.value
				}
				return nil
			})
		case 11, 12:
			value, err := nativeProtoTime(blob, field.offset, field.length, true)
			if err != nil {
				return err
			}
			if previous, exists := seenDurations[field.number]; exists && previous != value {
				return errors.New("原生计时字段冲突")
			}
			seenDurations[field.number] = value
			previous := &g.ttft
			if field.number == 12 {
				previous = &g.stream
			}
			if previous.known && *previous != value {
				return errors.New("原生计时字段冲突")
			}
			*previous = value
		}
		return nil
	})
}
