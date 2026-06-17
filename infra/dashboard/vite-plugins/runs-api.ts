import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function resolveOutputsRoot(): string {
  const env = process.env.MACOSWORLD_OUTPUTS_DIR
  if (env) return path.resolve(env)
  return path.resolve(__dirname, '..', '..', '..', 'outputs', 'runs')
}

function resolveTasksRoot(): string {
  // repo-root/infra/cli/tasks — task definitions hold the instruction + grading.
  return path.resolve(__dirname, '..', '..', 'cli', 'tasks')
}

/** Read a task definition by id, searching across category dirs. */
async function readTaskDef(tasksRoot: string, taskId: string) {
  let categories: import('fs').Dirent[]
  try {
    categories = await fs.readdir(tasksRoot, { withFileTypes: true })
  } catch {
    return null
  }
  for (const cat of categories) {
    if (!cat.isDirectory()) continue
    const file = path.join(tasksRoot, cat.name, `${taskId}.json`)
    let raw: string
    try {
      raw = await fs.readFile(file, 'utf8')
    } catch {
      continue
    }
    const d = JSON.parse(raw)
    const pre = d.pre_command
    return {
      task_id: taskId,
      category: cat.name,
      instruction: d?.task?.en ?? d?.task ?? '',
      pre_command: typeof pre === 'string' ? pre : pre?.en ?? '',
      grading_command: Array.isArray(d.grading_command) ? d.grading_command : [],
    }
  }
  return null
}

async function listRuns(root: string) {
  let entries: import('fs').Dirent[]
  try {
    entries = await fs.readdir(root, { withFileTypes: true })
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw err
  }

  const runs = await Promise.all(
    entries
      .filter((d) => d.isDirectory())
      .map(async (d) => {
        const runDir = path.join(root, d.name)
        const stat = await fs.stat(runDir)

        let nTasks = 0
        let summary: unknown = null
        try {
          const summaryRaw = await fs.readFile(path.join(runDir, 'summary.json'), 'utf8')
          summary = JSON.parse(summaryRaw)
          if (Array.isArray(summary)) nTasks = summary.length
        } catch {
          const subdirs = await fs.readdir(runDir, { withFileTypes: true })
          nTasks = subdirs.filter((s) => s.isDirectory()).length
        }

        return {
          run_id: d.name,
          n_tasks: nTasks,
          mtime: stat.mtimeMs,
          has_summary: summary !== null,
        }
      }),
  )

  runs.sort((a, b) => b.mtime - a.mtime)
  return runs
}

async function readRunSummary(root: string, runId: string) {
  const runDir = path.join(root, runId)
  try {
    const raw = await fs.readFile(path.join(runDir, 'summary.json'), 'utf8')
    return JSON.parse(raw)
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err
  }

  let subdirs: import('fs').Dirent[]
  try {
    subdirs = await fs.readdir(runDir, { withFileTypes: true })
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return null
    throw err
  }
  const results = await Promise.all(
    subdirs
      .filter((d) => d.isDirectory())
      .map(async (d) => {
        try {
          const resultRaw = await fs.readFile(
            path.join(runDir, d.name, 'result.json'),
            'utf8',
          )
          return JSON.parse(resultRaw)
        } catch {
          return { task_id: d.name, status: 'unknown' }
        }
      }),
  )
  return results
}

function sendJson(res: import('http').ServerResponse, status: number, body: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(body))
}

export function runsApi(): Plugin {
  return {
    name: 'runs-api',
    configureServer(server) {
      const outputsRoot = resolveOutputsRoot()
      const tasksRoot = resolveTasksRoot()
      server.config.logger.info(`[runs-api] serving runs from ${outputsRoot}`)

      server.middlewares.use('/api/taskdef', async (req, res, next) => {
        if (req.method !== 'GET') return next()
        try {
          const url = new URL(req.url || '/', 'http://x')
          const raw = url.pathname.replace(/^\/+|\/+$/g, '')
          // Strip a `__tNN` trial suffix: the task definition on disk is keyed by
          // the base id, but trajectory dirs carry the per-trial suffix.
          const taskId = raw.replace(/__t\d+$/, '')
          if (!taskId) return sendJson(res, 400, { error: 'taskId required' })
          const def = await readTaskDef(tasksRoot, taskId)
          if (def === null) return sendJson(res, 404, { error: 'task not found' })
          return sendJson(res, 200, def)
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err)
          return sendJson(res, 500, { error: message })
        }
      })

      server.middlewares.use('/api/runs', async (req, res, next) => {
        if (req.method !== 'GET') return next()
        try {
          const url = new URL(req.url || '/', 'http://x')
          const rest = url.pathname.replace(/^\/+|\/+$/g, '')
          if (!rest) {
            const runs = await listRuns(outputsRoot)
            return sendJson(res, 200, runs)
          }
          const [runId, ...extra] = rest.split('/')
          if (extra.length > 0) return next()
          const summary = await readRunSummary(outputsRoot, runId)
          if (summary === null) return sendJson(res, 404, { error: 'run not found' })
          return sendJson(res, 200, summary)
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err)
          return sendJson(res, 500, { error: message })
        }
      })
    },
  }
}
