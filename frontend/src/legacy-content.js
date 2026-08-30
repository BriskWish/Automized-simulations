/**
 * Content migrated from the configuration, beginner guide, and about tabs in
 * app.py.  It is intentionally presentation-free so the assistant-ui surface
 * can render it in an accordion or another native React layout.
 *
 * Rich paragraph parts use: text, strong, code, and link.
 */

const text = (value) => ({ type: "text", value });
const strong = (value) => ({ type: "strong", value });
const code = (value) => ({ type: "code", value });
const link = (value, href) => ({ type: "link", value, href, external: true });

export const LEGACY_CONFIGURATION = {
  title: "配置",
  sections: [
    {
      id: "llm",
      title: "1. LLM 配置",
      notice: {
        source: "llm-config-notice",
        fallback: "Agent 制作者不会以任何方式获取您的 API Key。您提供的 LLM 只会在您电脑本地的 .env 配置。",
      },
      paragraphs: [
        [text("本版本仅支持用户自配 OpenAI-compatible API Key；托管网关配置入口已冻结。")],
      ],
      fields: [
        {
          id: "apiKey",
          label: "OpenAI-compatible API Key",
          inputType: "password",
          placeholder: "sk-***你的API key***",
          help: "支持 OpenAI、DeepSeek、阿里云百炼、智谱 AI、月之暗面（Kimi）等厂商。Agent 制作者不会以任何方式获取您的 API-key。",
        },
        {
          id: "baseUrl",
          label: "Base URL",
          placeholder: "https://api.deepseek.com 或 http://localhost:8000/v1",
          help: "按服务提供商的 URL 文档填写。部分兼容/中转站服务不能使用网址直填，需在 URL 结尾添加 /v1。",
        },
        {
          id: "model",
          label: "Model",
          help: "只能填写服务提供商支持的模型，请注意横线、下划线或大小写格式。",
        },
      ],
      actions: [
        { id: "test-llm-connection", label: "测试连接", kind: "secondary" },
        { id: "save-llm-config", label: "保存", kind: "primary" },
      ],
      status: { source: "llm-config-status", initial: "" },
    },
    {
      id: "dependency-preflight",
      title: "2. 运行依赖预检",
      paragraphs: [
        [text("检查本机软件和内置模组。检测到的软件会自动保存为本项目默认配置；不会覆盖已有配置，也不会阻止本地任务启动。")],
      ],
      actions: [
        { id: "run-dependency-preflight", label: "预检运行依赖", kind: "secondary" },
      ],
      status: { source: "dependency-preflight-status", initial: "尚未执行依赖预检。" },
    },
  ],
};

export const BEGINNER_GUIDE_SECTIONS = [
  {
    id: "agent-overview",
    title: "零、Agent 的概况",
    paragraphs: [
      [text("Willy 基于 Linux/WSL2 系统，由方案助理和运行助理协作完成分子动力学工作流。方案助理负责把自然语言需求整理为可确认的体系与协议；运行助理负责展示当前工程状态、阶段产物和需要用户确认的调整方案。")],
      [text("二者功能、聊天记录不互通。")],
      [text("位置：在“任务”页上方左右两侧使用两名助理。只有确认最新方案后，流水线才会启动。")],
      [text("Agent 严格执行结构优化-电荷设置-拓扑生成-模拟参数生成-进行模拟的操作链路，仅在参数初始配置或报错时发生 LLM 介入。")],
      [text("MD 模拟过程中遵循以下步骤：1. 能量最小化（Energy Minimization, em）；2. 梯度退火平衡（Gradient Annealing Equilibrium, EQ）；3. 生产阶段（Production, prod）。")],
    ],
  },
  {
    id: "external-dependencies",
    title: "一、运行 Agent 最少需要的外置依赖",
    paragraphs: [
      [
        text("实现完整工作流，至少需要本机具备：1. 一个用于量化计算的软件（Gaussian16、Gaussian09 或 ORCA）；2. "),
        strong("GROMACS 2022.0 或更高版本"),
        text("。如果您没有这些软件，需要自行安装，Willy 只检验软件可用性。实际使用哪个后端/力场，由用户确认方案决定。"),
      ],
      [
        strong("推荐 ORCA："),
        text("建议使用 ORCA 6.x 的 Linux x86_64 官方发行包。在 "),
        link("ORCA Forum", "https://orcaforum.kofo.mpg.de/"),
        text(" 注册并接受许可后下载，解压至本机目录；将该安装目录填入 "),
        code("WILLY_ORCA_HOME"),
        text("，或将 "),
        code("orca"),
        text(" 放入 PATH。请勿使用来源不明的重打包二进制。"),
      ],
      [
        strong("LigParGen（仅 OPLS-AA 路径）："),
        text("除 LigParGen 外，还需要带格式插件和数据文件的完整 Open Babel 3、C shell（"),
        code("csh"),
        text("）以及 BOSS 运行环境。内置的精简 Open Babel 运行时不能替代完整安装。对应位置为本机 "),
        code(".env"),
        text("："),
        code("WILLY_LIGPARGEN_BIN"),
        text("、"),
        code("WILLY_OBABEL_BIN"),
        text("、"),
        code("WILLY_CSH_BIN"),
        text("、"),
        code("WILLY_BOSS_HOME"),
        text("；未选择 OPLS-AA 时无需配置这一组依赖。"),
      ],
      [text("位置：LLM 服务在“配置”页填写并测试；本机软件的可用情况会在工程启动后记录，并由运行助理报告。")],
    ],
  },
  {
    id: "initial-system",
    title: "二、体系的初始确定",
    paragraphs: [
      [text("在“任务”页的方案助理输入体系组分、数量、目标温度、模拟时长和偏好的量子或力场后端。需要使用自有结构时，通过同页的“上传结构”加入结构，再让方案助理生成方案。")],
      [text("位置：方案摘要会出现在方案助理对话中；请先核对分子、电荷、自旋、组分数量和模拟目标，再确认运行。")],
    ],
  },
  {
    id: "modifiable-parameters",
    title: "三、可供修改的参数",
    subsections: [
      {
        title: "3.1 量子层",
        paragraphs: [[text("可调整量化计算后端的分子电荷与自旋、结构优化与单点计算要求。位置：在“任务”页向方案助理提出修改，并在新的方案摘要中核对。")]],
      },
      {
        title: "3.2 拓扑层",
        paragraphs: [[text("可调整力场选择和组分对应关系。位置：在方案确认前通过方案助理修改；运行中出现拓扑错误时，运行助理会报告处理状态。")]],
      },
      {
        title: "3.3 模拟层",
        paragraphs: [[text("可调整初始密度、盒子尺寸、EM/EQ/PROD 协议、温度、压力、耦合方式、时间步长、生产时长和输出精度。位置：在方案确认前提出；EQ 验收失败后的协议改动必须在运行助理中再次确认。")]],
      },
    ],
  },
  {
    id: "llm-errors",
    title: "四、LLM 的报错处理",
    paragraphs: [
      [text("若方案助理无法响应或无法调用工具，先在“配置”页核对 API Key、Base URL 和 Model，再使用“测试连接”。测试不会保存配置，也不会自动补全 Base URL 的 "), code("/v1"), text("。")],
      [text("若问题发生在科学计算阶段，请查看运行助理的新状态气泡和待确认方案；不要把聊天中的“中止”当作控制命令，停止操作只通过“中止流水线”按钮完成。")],
    ],
  },
  {
    id: "structures",
    title: "五、获取结构",
    paragraphs: [
      [text("在“任务”页左下的“可视化”区域，先在左栏选择运行目录，再在右栏选择该运行目录下的 PDB 或 MOL2 文件。已验收的 EM、EQ、PROD 阶段可生成用于查看的结构产物。")],
      [text("位置：可视化右侧的图表绘制区域仍在开发中；结构文件名称保留完整名称或运行内相对路径，便于区分历史工程。")],
    ],
  },
  {
    id: "other",
    title: "六、其他",
    paragraphs: [[text("RDF、RMSD 等可视化图表仍为后续功能开发，欢迎各位用户提出宝贵意见和建议！")]],
  },
];

export const ABOUT_WILLY = {
  title: "关于 Willy",
  introduction: {
    title: "Willy：AI 驱动的小分子 Gromacs 模拟工具",
    paragraphs: [[text("Willy 是基于 Gromacs 软件的 MD 模拟自动化 Agent 组。目前它有两名员工：Willy-方案助理和 Willy-运行助理。")]],
  },
  sections: [
    {
      id: "contributors",
      title: "参与者",
      items: [
        "项目整体统筹：小w",
        "架构、交付、质量：ChatGPT 5.6-terra",
        "前端、文档、测试、Tools 等领域工程：ChatGPT 5.6-terra，DeepSeek V4 Pro",
      ],
    },
    {
      id: "capabilities",
      title: "Willy 能做什么？",
      items: [
        "Willy-方案助理会根据您的自然语言描述生成配置，执行从分子结构优化到 GROMACS 模拟的全链路。",
        "Willy-运行助理负责监控和记录整个模拟流程，并在出现问题时为您提供建议、解决方案和重跑续跑计划。",
        "您还可以通过可视化界面看到运行中产出的分子结构与 MD 盒子结果。",
      ],
    },
    {
      id: "roadmap",
      title: "后续规划",
      items: [
        "扩展更多的工具链集成。",
        "提供历史运行对比，以及受控的后处理和图表能力。",
      ],
    },
  ],
  officialAccount: {
    label: "公众号：小w的学习笔记",
    qrSource: "assets/qrcode_for_gh_7df1329939c6_258.jpg",
  },
  thirdPartyNotices: {
    title: "第三方组件引用与著作权",
    paragraphs: [
      [text("Willy 集成并编排第三方科学软件，但 Willy 作者不拥有其原始项目的著作权。")],
      [text("研究工作如使用了 Willy 集成的 Sobtop 拓扑文件生成、Multiwfn 电荷生成，请至少引用以下文献或网站：")],
    ],
    citationGroups: [
      {
        label: "Sobtop 与 Multiwfn",
        citations: [
          [text("Tian Lu, Sobtop, Version [当前版本], "), link("http://sobereva.com/soft/Sobtop", "http://sobereva.com/soft/Sobtop"), text(" (accessed on 日 月 年)")],
          [text("Tian Lu, Feiwu Chen, Multiwfn: A Multifunctional Wavefunction Analyzer, Journal of Computational Chemistry 33, 580-592 (2012). DOI: 10.1002/jcc.22885")],
          [text("Tian Lu, A comprehensive electron wavefunction analysis toolbox for chemists, Multiwfn, Journal of Chemical Physics 161, 082503 (2024). DOI: 10.1063/5.0216272")],
        ],
      },
      {
        label: "Packmol",
        introduction: [text("研究工作如使用了 Willy 集成的 Packmol 建盒组件，请至少引用以下文献：")],
        citations: [
          [text("L. Martinez, R. Andrade, E. G. Birgin, J. M. Martinez, Packmol: A package for building initial configurations for molecular dynamics simulations, Journal of Computational Chemistry 30, 2157-2164 (2009). DOI: 10.1002/jcc.21224")],
          [text("J. M. Martinez, L. Martinez, Packing optimization for the automated generation of complex system's initial configurations for molecular dynamics and docking, Journal of Computational Chemistry 24, 819-825 (2003). DOI: 10.1002/jcc.10216")],
        ],
      },
    ],
    closing: [text("使用 Sobtop、Packmol 或其他第三方组件时，使用者还应遵守其各自的许可、分发和引用要求。Willy 对这些组件仅提供集成与工作流编排，不主张其原始软件、文档或学术成果的著作权。")],
  },
};

export const LEGACY_CONTENT = {
  configuration: LEGACY_CONFIGURATION,
  beginnerGuide: BEGINNER_GUIDE_SECTIONS,
  about: ABOUT_WILLY,
};
