# 最终人工审核记录

模型或自动测试不能代替人工接受决定。当前人工审核已经完成，权威记录为
`outputs/manifest.json`；该文件当前为 `release_ready=true`、`known_blockers=[]`。

## 已接受内容

| 分组 | 最终内容 | 人工审核结果 |
|---|---|---|
| 视觉参考 | 1 张角色身份参考图 | 已接受并绑定 SHA-256 |
| T1–T6 | 每章 1 张独立场景图 | 六章均已接受 |
| T2 | 早安语音 WAV | Chrome 实机可播放，约 7.570 秒 |
| T5 | 晚安语音 WAV | Chrome 实机可播放，约 5.132 秒 |
| 声音参考 | macOS Tingting 参考音色 | 已试听并接受 |

每项最终媒体均包含 `decision_id`、`decided_by=human`、理由、路径、大小和 SHA-256。
网页只读取 manifest 中已接受且文件哈希匹配的媒体。

## 已拒绝内容

manifest 保留了五项明确拒绝记录：

- T5 语速过快候选；
- T5 地域音色不匹配候选；
- T5 语速偏慢候选；
- 浏览器不支持的 T2 AIFF；
- 浏览器不支持的 T5 AIFF。

拒绝记录不会从历史中删除，也不会出现在玩家最终媒体中。

## 复核命令

```bash
jq '{release_ready, known_blockers, final_media, rejected_results}' outputs/manifest.json
PYTHONPATH=src .venv/bin/python -m nining.release_audit . --smoke
```

禁止手工把候选改成 `accepted=true`。未来若重新生成素材，仍须核对真实文件、SHA-256、
连续性和浏览器可用性，再通过审核服务写入新的人工决定。
