# 微信小程序语音验收记录

> 当前状态：**Pending（待验收）**。自动化只能核对配置、状态和错误计数，不能证明扬声器实际可听。Android 与 iPhone 两台真机的全部项目都通过后，才允许面向患者发布。

## 构建与自动化证据

| 项目 | 记录 |
| --- | --- |
| 提交号 | Pending |
| 自动冒烟报告（本地忽略文件） | `reports/acceptance/wechat-smoke-voice.json` |
| 微信开发者工具版本 | Pending |
| 调试基础库版本 | Pending |
| 自动化结果 | Pending |
| 自动化证据文件名 | Pending |

自动化检查与人工听测必须分开记录。自动化仅核对：私有配置中是否存在真实 AppID（只记布尔值）、WechatSI `0.3.5` / `wx069ba97219f66d99` 声明、包内本地音频数量为 0、编译状态、控制台和异常计数，以及欢迎语音 650ms 时间窗后 `recordState` 为 `idle` 且 `voiceMode` 为空。自动化不得点击麦克风、接受隐私提示、启动录音，也不得声称听到了声音。

## 后台与隐私前置项（人工）

| 项目 | 结果（Pass / Fail / Pending） | 证据文件名 |
| --- | --- | --- |
| 小程序后台已添加并启用 WechatSI 插件 | Pending | Pending |
| 后台隐私保护指引已声明临时麦克风与语音识别处理 | Pending | Pending |
| 首次语音操作前的用户同意流程可见且可拒绝 | Pending | Pending |

不得在本文件记录 AppID、OpenID、识别文字、患者输入、临时 TTS URL、完整日志或截图内容；只记录结果和证据文件名。原始报告和截图仅在已忽略的 `reports/` 目录本地保存；任何录音、识别文字、患者输入、临时 TTS URL 均不留存，也不得作为证据。

## 真机信息

| 平台 | 机型 | OS 版本 | 微信版本 | 基础库版本 | 测试人 | 日期 | 总结果 | 证据文件名 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Android | Pending | Pending | Pending | Pending | Pending | Pending | Pending | Pending |
| iPhone | Pending | Pending | Pending | Pending | Pending | Pending | Pending | Pending |

## 12 项行为矩阵（必须人工完成）

每格结果只能填写 `Pass`、`Fail` 或 `Pending`；证据列只填写文件名，不写识别内容、目的地、URL 或设备账号。

| # | 行为与通过标准 | Android 结果 | Android 证据文件名 | iPhone 结果 | iPhone 证据文件名 |
| --- | --- | --- | --- | --- | --- |
| 1 | 欢迎 TTS：进入页面后播报一次，且不自动启动录音 | Pending | Pending | Pending | Pending |
| 2 | 导航步骤 TTS：起步、转向、换层及到达提示顺序正确 | Pending | Pending | Pending | Pending |
| 3 | 重播：只重复当前提示，不推进导航状态 | Pending | Pending | Pending | Pending |
| 4 | 文字导诊回复：输入文字后的回复可播报，连续回复不串音 | Pending | Pending | Pending | Pending |
| 5 | 语音导诊回复：用户明确点击后才录音，最终识别回复可播报 | Pending | Pending | Pending | Pending |
| 6 | 权限拒绝：拒绝麦克风或隐私授权后保留可见文字导航，不循环请求 | Pending | Pending | Pending | Pending |
| 7 | 断网失败：TTS/识别不可用时只显示文字状态，不播放旧音频或阻塞导航 | Pending | Pending | Pending | Pending |
| 8 | 隐藏/卸载取消：切后台及退出页面后，合成、播放和录音均停止且不晚到回调 | Pending | Pending | Pending | Pending |
| 9 | 合成/播放超时：分别等待合成超时与播放超时，均安全降级到文字且可继续操作 | Pending | Pending | Pending | Pending |
| 10 | 系统静音：静音时界面不误报“已听到”，恢复音量后新提示可正常播放 | Pending | Pending | Pending | Pending |
| 11 | 音频焦点中断：来电、媒体或系统提示中断后无叠音，后续操作可恢复 | Pending | Pending | Pending | Pending |
| 12 | 快速重复操作：连续重播、暂停/继续、切换步骤或连续回复时，只保留最新合法队列 | Pending | Pending | Pending | Pending |

## 发布结论

| 项目 | 记录 |
| --- | --- |
| Android 12 项全部通过 | Pending |
| iPhone 12 项全部通过 | Pending |
| 后台插件与隐私前置项全部通过 | Pending |
| 面向患者发布结论 | Pending（禁止发布） |

只有上述三项均为 `Pass`，且对应证据文件名齐全时，发布结论才能改为 `Pass`。任何自动化结果都不能替代两台真机的实际听测。
