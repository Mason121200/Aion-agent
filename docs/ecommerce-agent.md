# 电商垂直领域工作 Agent（feat/ecommerce-agent）

> 定位：基于 Aion 通用底座（工具注册 / ReAct 循环 / 认知记忆 / Skill 扩展）新增的第二个垂直场景。
> 首个切片：把 AI_chat_web 的 Agnes 图片/视频生成实现提取为独立客户端，以 Skill 形式接入底座。
> 分支：`feat/ecommerce-agent`（自 `main` 切出，2026-08-10）。

## 一、为什么这么拆

- Aion 底座把「新能力 = 往 skills/catalog.py 加一个 Skill」定为扩展协议，底座代码零改动。
- AI_chat_web 的 Agnes 生成实现（generation_service.py / tools/media/gen.py / routers/generation.py）是**项目内硬编码**：URL、模型名、鉴权都写死，且依赖该项目的 settings/store。
- 提取目标：把「调用 Agnes 生成商品图/视频」变成 Aion 里一个可被 LLM 调用的工具集，后续电商业务（商品主图工作流、SKU 批量出图、视频轮播）都长在这个 Skill 上。

## 二、目录与职责

```
aion_agent/ecommerce/
├── agnes_client.py       # Agnes HTTP 客户端：文生图 / 图生图 / 视频提交 / 视频状态
└── ecommerce_tools.py    # 4 个电商工具 handler + OpenAI schema + 注册函数
```

- `agnes_client.py`：提取自 `generation_service.py`，差异点：
  - 配置改为环境变量 `AGNES_API_KEY`（必填）/ `AGNES_BASE_URL`（默认 `https://apihub.agnes-ai.com/v1`）；
  - 统一错误类型 `AgnesError`，HTTP 非 200 / 业务 error / 缺 URL 都收敛为异常；
  - 图片模型 `agnes-image-2.1-flash`、视频模型 `agnes-video-v2.0`；
  - 视频状态查询端点保持根路径 `/agnesapi?video_id=`。
- `ecommerce_tools.py`：工具清单（4 个）：
  1. `generate_product_image` 文生图商品图（prompt / size）
  2. `edit_product_image` 图生图编辑（prompt / image_url / size）
  3. `submit_product_video` 商品视频任务提交（prompt / image_urls / 帧数 / 帧率）
  4. `generation_status` 视频生成状态查询（video_id）

## 三、接入方式

`skills/catalog.py` 新增 ecommerce Skill（始终安装，不依赖 repo）：

```python
Skill(
    name="ecommerce",
    version="1.0.0",
    description="电商场景：商品图生成 / 商品图编辑 / 商品视频（Agnes）",
    tools=["generate_product_image", "edit_product_image",
           "submit_product_video", "generation_status"],
    register_func=register_ecommerce_tools,
)
```

- handler 均为同步函数，由 ToolExecutor 在后台线程执行（带超时熔断）。
- 图片生成为同步调用（超时 120s）；视频为异步任务（提交后由 `generation_status` 轮询）。

## 四、配置

```bash
# .env / 环境变量
AGNES_API_KEY=your_agnes_key      # 与 AI_chat_web 共用同一个 key
AGNES_BASE_URL=https://apihub.agnes-ai.com/v1   # 可选，默认即此值
```

## 五、测试

`tests/test_ecommerce_agnes.py`（12 用例，全部 mock 网络）：
- 客户端：成功返回 URL / 图生图 extra_body.image / 缺 key / HTTP 401 / 业务 error / 视频提交 / 状态端点；
- 工具层：注册 4 工具 / handler 调用与参数校验；
- catalog：build_default_skills 含 ecommerce。

## 六、电商 MVP 建议范围（待本人拍板）

本切片只落地「生成能力」。垂直业务按需切片推进，候选（按价值排序）：

1. **商品主图工作流**：白底图 → 场景图 → 多尺寸导出（生成 + 图生图已够）；
2. **商品批量出图**：读取商品表格（SKU 名/卖点）→ 批量生成主图（要加 ecommerce_repo 持久化）；
3. **商品图文案**：标题 / 卖点 / 详情描述（纯 LLM，无新依赖）；
4. **商品视频轮播**：视频提交 + 状态轮询 + 结果入库；
5. **Web UI 入口**：学习页同级加电商页，展示生成历史。

> 约束（沿用个人学习助手 MVP 的经验）：每轮只做一个切片，复杂度低于执行阈值；
> 范围先守住自用 / 作品集，不做多用户与商业化。

## 七、已知风险

- 图片 URL 有有效期：Agnes 返回临时对象地址，长时间保存需下载到本地（v1 先直传 URL，由调用方决定是否落盘）。
- `edit_product_image` 依赖 Agnes 图生图对参考图 URL 的可访问性：本地图片需先有公网/内网可达地址。
- 无 AGNES_API_KEY 时工具报错信息明确（配置缺失），不静默失败。
- 未做限流与计费保护：LLM 反复调用会消耗 Agnes 配额，后续可按切片加每日预算。