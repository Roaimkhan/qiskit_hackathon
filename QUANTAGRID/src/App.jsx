import { useEffect, useMemo, useState } from 'react'
import './App.css'

function collectNodes(node, x, y, parentId = null) {
  const current = { ...node, x, y, parentId }
  const childNodes = []

  if (!Array.isArray(node.children) || node.children.length === 0) {
    return [current]
  }

  const gap = node.children.length > 3 ? 180 : 220
  const startX = x - ((node.children.length - 1) * gap) / 2

  node.children.forEach((child, index) => {
    const childX = startX + index * gap
    const childY = y + 170
    childNodes.push(...collectNodes(child, childX, childY, node.id))
  })

  return [current, ...childNodes]
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
    if (!topology) return { nodes: [], edges: [] }

    const nodes = collectNodes(topology, 600, 80)
    const nodeMap = new Map(nodes.map((node) => [node.id, node]))
    const edges = []

    nodes.forEach((node) => {
      if (node.parentId && nodeMap.has(node.parentId)) {
        edges.push({
          from: nodeMap.get(node.parentId),
          to: node,
        })
      }
    })

    return { nodes, edges }
  }, [topology])

  if (loading) {
    return <div className="app-shell">Loading feeder topology...</div>
  }

  if (!topology) {
    return <div className="app-shell">Unable to load feeder topology.</div>
  }

  return (
    <div className="app-shell">
      <h1>Distribution Feeder Map</h1>

      <div className="topology-panel">
        <svg viewBox="0 0 1200 750" preserveAspectRatio="xMidYMid meet">
          {topologyData.edges.map((edge, index) => (
            <line
              key={`${edge.from.id}-${edge.to.id}-${index}`}
              x1={edge.from.x}
              y1={edge.from.y + 52}
              x2={edge.to.x}
              y2={edge.to.y}
              className="connection-line"
            />
          ))}

          {topologyData.nodes.map((node) => {
            const kindClass = node.node_type === 'feeder' ? 'node-feeder' : node.node_type === 'lateral' ? 'node-lateral' : 'node-transformer'

            return (
              <g key={node.id} transform={`translate(${node.x - 110}, ${node.y})`}>
                <rect className={`node-card ${kindClass}`} width="220" height="92" rx="12" />
                <text x="16" y="28" className="node-id">{node.id}</text>
                <text x="16" y="48" className="node-name">
                  {node.name || node.customer_type || node.node_type}
                </text>
                {node.load && (
                  <text x="16" y="66" className="node-meta">
                    Load: {node.load.value} {node.load.unit}
                  </text>
                )}
                {node.priority && (
                  <text x="16" y="82" className="node-meta priority">
                    Priority: {node.priority}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}

export default App
