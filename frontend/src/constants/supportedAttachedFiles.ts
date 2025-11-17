// To change the supported text format files, change the extension list below.
export const TEXT_FILE_EXTENSIONS = [
  '.txt',
  '.py',
  '.ipynb',
  '.js',
  '.jsx',
  '.html',
  '.css',
  '.java',
  '.cs',
  '.php',
  '.c',
  '.cpp',
  '.cxx',
  '.h',
  '.hpp',
  '.rs',
  '.R',
  '.Rmd',
  '.swift',
  '.go',
  '.rb',
  '.kt',
  '.kts',
  '.ts',
  '.tsx',
  '.m',
  '.scala',
  '.rs',
  '.dart',
  '.lua',
  '.pl',
  '.pm',
  '.t',
  '.sh',
  '.bash',
  '.zsh',
  '.csv',
  '.log',
  '.ini',
  '.config',
  '.json',
  '.proto',
  '.yaml',
  '.yml',
  '.toml',
  '.lua',
  '.sql',
  '.bat',
  '.md',
  '.coffee',
  '.tex',
  '.latex',
];

// Supported non-text file extensions which can be handled on Converse API.
// Ref: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_DocumentBlock.html
export const NON_TEXT_FILE_EXTENSIONS = [
  '.pdf',
  '.doc',
  '.docx',
  '.xls',
  '.xlsx',
  '.epub',
];

export const SUPPORTED_FILE_EXTENSIONS = [
  ...TEXT_FILE_EXTENSIONS,
  ...NON_TEXT_FILE_EXTENSIONS,
];

// Converse API limitations:
// You can include up to five documents. Each document’s size must be no more than 4.5 MB.
// Ref: https://awscli.amazonaws.com/v2/documentation/api/latest/reference/bedrock-runtime/converse.html
// export const MAX_FILE_SIZE_MB = 4.5;
// export const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
// export const MAX_ATTACHED_FILES = 5;

//export const MAX_FILE_SIZE_MB = 4.5;
export const MAX_FILE_SIZE_MB = 200;
export const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
export const MAX_ATTACHED_FILES = 5;

// Maximum total file size to send to lambda (Converse API)
// export const MAX_FILE_SIZE_TO_SEND_MB = 6; // 6 MB (Lambda response size limit)

export const MAX_FILE_SIZE_TO_SEND_MB = 200;
export const MAX_FILE_SIZE_TO_SEND_BYTES =
  MAX_FILE_SIZE_TO_SEND_MB * 1024 * 1024;

// Claude 4 models use invoke API instead of converse API and have higher file size limits
// Large PDFs (>50 pages or >2MB) are now processed with Docling for intelligent chunking
//export const CLAUDE_4_MAX_FILE_SIZE_MB = 50; // 50 MB limit per file (PDFs are chunked if needed)
export const CLAUDE_4_MAX_FILE_SIZE_MB = 200;
export const CLAUDE_4_MAX_FILE_SIZE_BYTES =
  CLAUDE_4_MAX_FILE_SIZE_MB * 1024 * 1024;

// Maximum total file size to send to lambda (Claude 4)
// Note: Large PDFs are automatically chunked by Docling, so they won't hit this limit
export const CLAUDE_4_MAX_FILE_SIZE_TO_SEND_MB = 200; // 200 MB total limit for Claude 4
export const CLAUDE_4_MAX_FILE_SIZE_TO_SEND_BYTES =
  CLAUDE_4_MAX_FILE_SIZE_TO_SEND_MB * 1024 * 1024;

// Claude 4 model detection
export const CLAUDE_4_MODELS = ['claude-v4-opus', 'claude-v4-sonnet'];

export const isClaude4Model = (model: string): boolean => {
  return CLAUDE_4_MODELS.includes(model);
};
