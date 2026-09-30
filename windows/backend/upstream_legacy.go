package backend

// Guarded retirement of the v2 route journal, matching ManagedRoute._restore_v2.
// Foreign configuration fields and comments remain byte-for-byte unchanged.
import (
	"bytes"
	"errors"
	"os"
	"reflect"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

func (p *UpstreamProxy) restoreLegacyJournal(j Row) error {
	path := ValueString(j["config"])
	original, ok := j["original"].(string)
	applied, aok := j["applied"].(string)
	if path == "" || !ok || !aok || len(original) > 2<<20 || len(applied) > 2<<20 {
		return errors.New("旧版上游恢复记录无效，已保留原配置")
	}
	current, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if len(current) > 2<<20 {
		return errors.New("Codex 配置超过容量限制")
	}
	var before, after, document map[string]any
	if toml.Unmarshal([]byte(original), &before) != nil || toml.Unmarshal([]byte(applied), &after) != nil || toml.Unmarshal(current, &document) != nil {
		return errors.New("旧版上游配置不是有效 TOML，已保留原配置")
	}
	restored := original
	if string(current) != applied {
		restored = string(current)
		for _, key := range []string{"openai_base_url", "model_provider"} {
			actual, present := document[key]
			owned, appliedPresent := after[key]
			if !present || !appliedPresent || !reflect.DeepEqual(actual, owned) {
				continue
			}
			var replacement *string
			if value, exists := before[key]; exists {
				if _, ok := value.(string); !ok {
					return errors.New("旧版模型路由字段无效")
				}
				_, line, found, e := upstreamPatch(original, nil, key, nil)
				if e != nil || !found {
					return errors.New("无法安全恢复旧版模型路由字段")
				}
				replacement = &line
			}
			var e error
			restored, _, _, e = upstreamPatch(restored, nil, key, replacement)
			if e != nil {
				return e
			}
		}
		providers := ValueRow(document["model_providers"])
		actual, exists := providers["codexio-upstream"]
		owned, wasOwned := ValueRow(after["model_providers"])["codexio-upstream"]
		if exists {
			if !wasOwned || !reflect.DeepEqual(actual, owned) {
				return errors.New("旧版上游路由被外部修改，已保留原配置")
			}
			restored, err = upstreamRemoveTable(restored, []string{"model_providers", "codexio-upstream"})
			if err != nil {
				return err
			}
		}
	}
	check, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if !bytes.Equal(check, current) {
		return errors.New("Codex 配置同时发生变化，请重试恢复")
	}
	if j["existed"] == false && strings.TrimSpace(restored) == "" {
		if err = os.Remove(path); err != nil && !os.IsNotExist(err) {
			return err
		}
	} else if restored != string(current) {
		if err = writePrivateFile(path, []byte(restored)); err != nil {
			return err
		}
	}
	return os.Remove(p.journalPath())
}

func upstreamRemoveTable(text string, target []string) (string, error) {
	newline := "\n"
	if strings.Contains(text, "\r\n") {
		newline = "\r\n"
	}
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	result := []string{}
	skip, removed := false, false
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		if strings.HasPrefix(trim, "[") {
			header := trim
			if at := strings.Index(header, "]"); at >= 0 {
				header = header[:at+1]
			}
			parts, ok := upstreamHeaderParts(header)
			if !ok {
				return "", errors.New("无法安全定位旧版上游配置表")
			}
			skip = len(parts) >= len(target)
			if skip {
				for i, k := range target {
					if parts[i] != k {
						skip = false
						break
					}
				}
			}
			if skip {
				removed = true
			}
		}
		if !skip {
			result = append(result, line)
		}
	}
	restored := strings.Join(result, newline)
	if !removed {
		var err error
		restored, _, removed, err = upstreamPatch(text, target[:len(target)-1], target[len(target)-1], nil)
		if err != nil {
			return "", err
		}
		if !removed {
			return "", errors.New("无法安全移除旧版上游路由")
		}
	}
	var verify map[string]any
	if toml.Unmarshal([]byte(restored), &verify) != nil {
		return "", errors.New("旧版路由恢复结果无效，已保留原配置")
	}
	if _, present := upstreamNested(verify, target); present {
		return "", errors.New("旧版上游路由未能安全移除")
	}
	return restored, nil
}
