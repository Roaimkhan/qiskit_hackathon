import { useEffect, useMemo, useState } from 'react'
import './App.css'

const NODE_WIDTH = 420
const NODE_HEIGHT = 160
const ROW_GAP = 90

function getNodeLabel(node) {
  return node.customer_type || node.name || node.id
}

function getTransformers(node) {
  if (!node) return []
  if (node.node_type === 'transformer') return [node]
  return (node.children || []).flatMap(getTransformers)
}

function buildVerticalTopology(root) {
  const allNodes = []
  const rootX = 760
  const rootY = 70
  const regularChildren = (root.children || []).filter((child) => child.node_type !== 'lateral')
  const lateralNode = (root.children || []).find((child) => child.node_type === 'lateral')

  const leftColumnNodes = regularChildren.slice(0, Math.ceil(regularChildren.length / 2))
  const rightColumnNodes = regularChildren.slice(Math.ceil(regularChildren.length / 2))

  const leftColumnX = 240
  const rightColumnX = 1180
  const firstColumnY = 300
  const secondColumnY = 300

  allNodes.push({
    ...root,
    parentId: null,
    x: rootX - NODE_WIDTH / 2,
    y: rootY,
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  })

  leftColumnNodes.forEach((child, index) => {
    allNodes.push({
      ...child,
      parentId: root.id,
      x: leftColumnX,
      y: firstColumnY + index * (NODE_HEIGHT + ROW_GAP),
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })
  })

  rightColumnNodes.forEach((child, index) => {
    allNodes.push({
      ...child,
      parentId: root.id,
      x: rightColumnX,
      y: secondColumnY + index * (NODE_HEIGHT + ROW_GAP),
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })
  })

  const regularBottom = Math.max(
    ...allNodes.filter((node) => node.parentId === root.id).map((node) => node.y + node.height),
    0,
  )

  if (lateralNode) {
    const lateralY = regularBottom + 160
    allNodes.push({
      ...lateralNode,
      parentId: root.id,
      x: rootX - NODE_WIDTH / 2,
      y: lateralY,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })

    const lateralChildren = (lateralNode.children || []).map((child, index) => ({
      ...child,
      parentId: lateralNode.id,
      x: rootX - NODE_WIDTH / 2 + (index === 0 ? -300 : 300),
      y: lateralY + 220,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    }))

    allNodes.push(...lateralChildren)
  }

  const width = 1600
  const maxY = Math.max(...allNodes.map((node) => node.y + node.height))
  const height = Math.max(1200, maxY + 180)

  const edges = []

  allNodes.forEach((node) => {
    if (!node.parentId) return
    const from = allNodes.find((candidate) => candidate.id === node.parentId)
    if (!from) return
    edges.push({ from, to: node })
  })

  return { nodes: allNodes, edges, width, height }
}

function buildConnectionPath(from, to) {
  const startX = from.x + NODE_WIDTH / 2
  const startY = from.y + NODE_HEIGHT * 0.72
  const endX = to.x + NODE_WIDTH / 2
  const endY = to.y + NODE_HEIGHT * 0.28

  if (Math.abs(startX - endX) < 30) {
    return `M ${startX} ${startY} L ${endX} ${endY}`
  }

  const bendY = Math.max(startY, endY) + 48
  return `M ${startX} ${startY} V ${bendY} H ${endX} V ${endY}`
}

function App() {
  const [topology, setTopology] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadShedding, setLoadShedding] = useState(null)
  const [transformerStates, setTransformerStates] = useState({})
  const [dispatching, setDispatching] = useState(false)
  const [dispatchMessage, setDispatchMessage] = useState('')
  const [dispatchMetrics, setDispatchMetrics] = useState(null)

  useEffect(() => {
    fetch('/get_topology')
      .then((response) => response.json())
      .then((data) => {
        setTopology(data)
        setLoadShedding(0)
        setTransformerStates(Object.fromEntries(getTransformers(data).map((transformer) => [transformer.id, true])))
        setDispatchMetrics(null)
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
      })
  }, [])

  const topologyData = useMemo(() => {
    if (!topology) return { nodes: [], edges: [], width: 1600, height: 1200 }
    return buildVerticalTopology(topology)
  }, [topology])

  const transformers = useMemo(() => getTransformers(topology), [topology])
  const currentTotalLoad = transformers.reduce((total, transformer) => total + Number(transformer.load?.value || 0), 0)
  const criticalLoad = transformers
    .filter((transformer) => transformer.constraint?.type === 'hard')
    .reduce((total, transformer) => total + Number(transformer.load?.value || 0), 0)
  const maximumSafeShedding = Math.max(0, currentTotalLoad - criticalLoad)
  async function executeDispatch() {
    setDispatching(true)
    setDispatchMessage('')
    try {
      const response = await fetch('/api/optimize-dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feeder_id: topology.id,
          load_shedding_kw: loadShedding,
          transformers: transformers.map((transformer) => ({
            id: transformer.id,
            load_kw: Number(transformer.load?.value || 0),
            recovery_ratio: Number(transformer.bill_recovery?.value || 0) / 100,
            is_critical: transformer.constraint?.type === 'hard',
          })),
        }),
      })
      if (!response.ok) throw new Error(`Dispatch failed (${response.status})`)
      const result = await response.json()
      if (!result.success || !result.transformer_states) {
        throw new Error('Dispatch response did not include transformer states.')
      }
      const nextStates = Object.fromEntries(
        Object.entries(result.transformer_states).map(([id, isOn]) => [id, Boolean(isOn)]),
      )
      setTransformerStates(nextStates)
      const activeTransformers = transformers.filter((transformer) => nextStates[transformer.id])
      const postDispatchLoad = activeTransformers.reduce((total, transformer) => total + Number(transformer.load?.value || 0), 0)
      const recoveryWeightedLoad = activeTransformers.reduce(
        (total, transformer) => total + Number(transformer.load?.value || 0) * Number(transformer.bill_recovery?.value || 0),
        0,
      )
      const criticalTransformers = transformers.filter((transformer) => transformer.constraint?.type === 'hard')
      const protectedCriticalCount = criticalTransformers.filter((transformer) => nextStates[transformer.id]).length
      const criticalProtection = criticalTransformers.length
        ? Math.round((protectedCriticalCount / criticalTransformers.length) * 100)
        : 100
      setDispatchMetrics({
        activeLoad: postDispatchLoad,
        targetLoad: Math.max(0, currentTotalLoad - loadShedding),
        targetMet: postDispatchLoad <= Math.max(0, currentTotalLoad - loadShedding) + 0.01,
        revenueRecovery: postDispatchLoad ? recoveryWeightedLoad / postDispatchLoad : 0,
        criticalProtection,
        solverStatus: result.optimization_info?.solver_status || 'QAOA completed',
      })
      setDispatchMessage('Dispatch completed.')
    } catch (error) {
      setDispatchMessage(error.message || 'Dispatch failed.')
    } finally {
      setDispatching(false)
    }
  }

  if (loading) {
    return <div className="app-shell">Loading feeder topology...</div>
  }

  if (!topology) {
    return <div className="app-shell">Unable to load feeder topology.</div>
  }

  return (
    <div className="app-shell">
      <div className="title-bar">
        <div>
          <p className="eyebrow">Power network overview</p>
          <h1>Distribution Feeder Map</h1>
        </div>
      </div>

      <div className="dashboard-layout">
      <div className="topology-panel">
        <svg
          viewBox={`0 0 ${topologyData.width} ${topologyData.height}`}
          preserveAspectRatio="xMidYMid meet"
          aria-label="Electrical feeder topology map"
        >
          {topologyData.edges.map((edge, index) => (
            <path
              key={`${edge.from.id}-${edge.to.id}-${index}`}
              d={buildConnectionPath(edge.from, edge.to)}
              className="connection-line"
            />
          ))}

          {topologyData.nodes.map((node) => {
            const kindClass =
              node.node_type === 'feeder'
                ? 'node-feeder'
                : node.node_type === 'lateral'
                  ? 'node-lateral'
                  : 'node-transformer'

            const summaryLabel =
              node.node_type === 'feeder'
                ? node.name
                : node.node_type === 'lateral'
                  ? node.name
                  : getNodeLabel(node)

            const loadText = node.load ? `${node.load.value} ${node.load.unit}` : 'N/A'

            return (
              <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
                <foreignObject x="0" y="0" width={NODE_WIDTH} height={NODE_HEIGHT + 220}>
                  <div className={`node-card ${kindClass} ${node.node_type === 'transformer' ? (transformerStates[node.id] ? 'transformer-on' : 'transformer-off') : ''}`}>
                    <div className="node-header">
                      <span className={`node-icon-wrap ${node.node_type === 'transformer' ? (transformerStates[node.id] ? 'indicator-on' : 'indicator-off') : ''}`} aria-label={node.node_type === 'transformer' ? (transformerStates[node.id] ? 'Transformer ON' : 'Transformer OFF') : 'Network node'}>
                        <svg viewBox="0 0 24 24" className="node-icon" role="img" aria-hidden="true">
                          <path d="M4 7.5a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-9Zm3.5 0v9m9-9v9M8 10h8M8 14h8" />
                          <path d="M12 3v2m0 16v2M3 12h2m14 0h2" opacity="0.18" />
                        </svg>
                      </span>

                      <div className="node-title">
                        <span className="node-id">{node.id}</span>
                        <span className="node-name">{summaryLabel}</span>
                      </div>

                      <div className="node-load">{loadText}</div>
                      {node.node_type === 'transformer' && (
                        <span className={`transformer-state ${transformerStates[node.id] ? 'state-on' : 'state-off'}`}>
                          {transformerStates[node.id] ? 'ON' : 'OFF'}
                        </span>
                      )}
                    </div>

                    <div className="node-details">
                      {node.priority && (
                        <div className="detail-row">
                          <span>Priority</span>
                          <strong>{node.priority}</strong>
                        </div>
                      )}

                      {node.bill_recovery && (
                        <div className="detail-row">
                          <span>Bill recovery</span>
                          <strong>
                            {node.bill_recovery.value}
                            {node.bill_recovery.unit}
                          </strong>
                        </div>
                      )}

                      {node.constraint && (
                        <div className="detail-row">
                          <span>Constraint</span>
                          <strong>
                            {node.constraint.type} · {node.constraint.variable}={node.constraint.value}
                          </strong>
                        </div>
                      )}

                      {node.customer_type && node.node_type !== 'feeder' && (
                        <div className="detail-row detail-note">
                          <span>Site</span>
                          <strong>{node.customer_type}</strong>
                        </div>
                      )}
                    </div>
                  </div>
                </foreignObject>
              </g>
            )
          })}
        </svg>
      </div>
      <aside className="control-panel" aria-label="Feeder control and stress test">
        <p className="eyebrow">Feeder controls</p>
        <h2>Control &amp; Stress-Test</h2>

        <div className="load-metric">
          <span>Total feeder load</span>
          <strong>{currentTotalLoad.toLocaleString()} <small>kW</small></strong>
        </div>

        <label className="capacity-label" htmlFor="load-shedding">
          <span>Load shedding</span>
          <strong>{loadShedding} kW</strong>
        </label>
        <input
          id="load-shedding"
          className="capacity-slider"
          type="range"
          min="0"
          max={Number(topology.maximum_load)}
          step="1"
          value={loadShedding}
          onChange={(event) => {
            setLoadShedding(Number(event.target.value))
            setDispatchMetrics(null)
          }}
          aria-valuetext={`${loadShedding} kilowatts to shed`}
        />
        <div className="slider-limits"><span>0 kW</span><span>{topology.maximum_load} kW</span></div>
        {loadShedding > maximumSafeShedding && (
          <p className="safety-warning" role="alert">
            Maximum safe shedding is {maximumSafeShedding.toLocaleString()} kW. {criticalLoad.toLocaleString()} kW is locked by critical infrastructure protection rules.
          </p>
        )}

        <button className="dispatch-button" type="button" onClick={executeDispatch} disabled={dispatching}>
          {dispatching ? 'Running dispatch…' : 'Execute Surgical Dispatch'}
        </button>
        {dispatchMessage && <p className="dispatch-message" role="status">{dispatchMessage}</p>}
        {dispatchMetrics && (
          <section className="metrics-bar" aria-label="Post-dispatch performance metrics">
            <div className="performance-metric">
              <span>Post-Dispatch Active Load</span>
              <strong>{dispatchMetrics.activeLoad.toLocaleString()} kW</strong>
              <small>{dispatchMetrics.targetMet ? 'Shedding target met' : `Target ≤ ${dispatchMetrics.targetLoad.toLocaleString()} kW not met due to protection limits`}</small>
            </div>
            <div className="performance-metric">
              <span>Revenue Recovery Efficiency</span>
              <strong>{dispatchMetrics.revenueRecovery.toFixed(1)}%</strong>
              <small>Load-weighted recovery across active transformers</small>
            </div>
            <div className="performance-metric">
              <span>Critical Infrastructure Status</span>
              <strong>{dispatchMetrics.criticalProtection}% Protected</strong>
              <small>Critical transformers kept ON</small>
            </div>
            <div className="performance-metric">
              <span>Solver Execution Status</span>
              <strong>{dispatchMetrics.solverStatus}</strong>
            </div>
          </section>
        )}
      </aside>
      </div>
    </div>
  )
}

export default App
