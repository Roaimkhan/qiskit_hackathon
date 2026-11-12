import { useEffect, useMemo, useState } from 'react'
import './App.css'

const NODE_WIDTH = 420
const NODE_HEIGHT = 160
const ROW_GAP = 90

function getNodeLabel(node) {
  return node.customer_type || node.name || node.id
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

  useEffect(() => {
    fetch('http://localhost:8000/get_topology')
      .then((response) => response.json())
      .then((data) => {
        setTopology(data)
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
                  <div className={`node-card ${kindClass}`}>
                    <div className="node-header">
                      <span className="node-icon-wrap" aria-label="transformer icon">
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
    </div>
  )
}

export default App
