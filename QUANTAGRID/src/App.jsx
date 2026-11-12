import { useEffect, useMemo, useState } from 'react'
import './App.css'

function getTransformers(node) {
  if (!node || typeof node !== 'object') return []
  const children = Array.isArray(node.children) ? node.children : []
  const currentTransformer = node.node_type === 'transformer' ? [node] : []
  return currentTransformer.concat(children.flatMap(getTransformers))
}

function App() {
  const [topology, setTopology] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadShedding, setLoadShedding] = useState(null)
  const [transformerStates, setTransformerStates] = useState({})
  const [dispatching, setDispatching] = useState(false)
  const [dispatchMessage, setDispatchMessage] = useState('')
  const [dispatchMetrics, setDispatchMetrics] = useState(null)
  const [activePanelTab, setActivePanelTab] = useState('quantum')
  const [killSwitchMessage, setKillSwitchMessage] = useState('')

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

  const transformers = useMemo(() => getTransformers(topology), [topology])
  const currentTotalLoad = transformers.reduce((total, transformer) => total + Number(transformer.load?.value || 0), 0)
  const criticalLoad = transformers
    .filter((transformer) => transformer.constraint?.type === 'hard')
    .reduce((total, transformer) => total + Number(transformer.load?.value || 0), 0)
  const maximumSafeShedding = Math.max(0, currentTotalLoad - criticalLoad)
  const feederIsOff = transformers.length > 0 && transformers.every((transformer) => !transformerStates[transformer.id])

  function tripFeeder() {
    setTransformerStates(Object.fromEntries(transformers.map((transformer) => [transformer.id, false])))
    setDispatchMetrics(null)
    setDispatchMessage('')
    setKillSwitchMessage('Feeder shutdown simulated. All transformer cards are OFF.')
  }

  function switchPanelTab(tab) {
    if (tab === activePanelTab) return
    if (tab === 'quantum' || tab === 'classical') {
      setTransformerStates(Object.fromEntries(transformers.map((transformer) => [transformer.id, true])))
      setDispatchMetrics(null)
      setDispatchMessage('')
      setKillSwitchMessage('')
    }
    setActivePanelTab(tab)
  }

  async function executeDispatch(algorithm = 'quantum') {
    setDispatching(true)
    setDispatchMessage('')
    setKillSwitchMessage('')
    try {
      const dispatchEndpoints = {
        quantum: '/api/optimize-dispatch',
        classical: '/api/classical-dispatch',
        exact: '/api/exact-dispatch',
      }
      const endpoint = dispatchEndpoints[algorithm] || dispatchEndpoints.quantum
      const response = await fetch(endpoint, {
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
      <main className="feeder-grid-panel" aria-label="Feeder transformer cards">
        <div className="transformer-grid">
          {transformers.map((node) => (
            <article
              key={node.id}
              className={`node-card node-transformer ${transformerStates[node.id] ? 'transformer-on' : 'transformer-off'}`}
            >
              <div className="node-header">
                <span
                  className={`node-icon-wrap ${transformerStates[node.id] ? 'indicator-on' : 'indicator-off'}`}
                  aria-label={transformerStates[node.id] ? 'Transformer ON' : 'Transformer OFF'}
                >
                  <svg viewBox="0 0 24 24" className="node-icon" role="img" aria-hidden="true">
                    <path d="M4 7.5a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-9Zm3.5 0v9m9-9v9M8 10h8M8 14h8" />
                    <path d="M12 3v2m0 16v2M3 12h2m14 0h2" opacity="0.18" />
                  </svg>
                </span>

                <div className="node-title">
                  <span className="node-id">{node.id}</span>
                  <span className="node-name">{node.customer_type || node.name || node.id}</span>
                </div>

                <div className="node-load">{node.load ? `${node.load.value} ${node.load.unit}` : 'N/A'}</div>
                <span className={`transformer-state ${transformerStates[node.id] ? 'state-on' : 'state-off'}`}>
                  {transformerStates[node.id] ? 'ON' : 'OFF'}
                </span>
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
                    <strong>{node.bill_recovery.value}{node.bill_recovery.unit}</strong>
                  </div>
                )}

                {node.constraint && (
                  <div className="detail-row">
                    <span>Constraint</span>
                    <strong>{node.constraint.type} · {node.constraint.variable}={node.constraint.value}</strong>
                  </div>
                )}

                {node.customer_type && (
                  <div className="detail-row detail-note">
                    <span>Site</span>
                    <strong>{node.customer_type}</strong>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      </main>
      <aside className="control-panel" aria-label="Feeder control and stress test">
        <div className="control-panel-tabs" role="tablist" aria-label="Feeder panel tabs">
          <button
            id="kill-switch-tab"
            className={`control-tab ${activePanelTab === 'kill' ? 'active' : ''}`}
            type="button"
            role="tab"
            aria-selected={activePanelTab === 'kill'}
            aria-controls="kill-switch-panel"
            onClick={() => switchPanelTab('kill')}
          >
            Kill Switch
          </button>
          <button
            id="quantum-tab"
            className={`control-tab ${activePanelTab === 'quantum' ? 'active' : ''}`}
            type="button"
            role="tab"
            aria-selected={activePanelTab === 'quantum'}
            aria-controls="quantum-panel"
            onClick={() => switchPanelTab('quantum')}
          >
            Quantum Algorithm!
          </button>
          <button
            id="classical-tab"
            className={`control-tab ${activePanelTab === 'classical' ? 'active' : ''}`}
            type="button"
            role="tab"
            aria-selected={activePanelTab === 'classical'}
            aria-controls="classical-panel"
            onClick={() => switchPanelTab('classical')}
          >
            Classical Algorithm
          </button>
          <button
            id="exact-tab"
            className={`control-tab ${activePanelTab === 'exact' ? 'active' : ''}`}
            type="button"
            role="tab"
            aria-selected={activePanelTab === 'exact'}
            aria-controls="exact-panel"
            onClick={() => switchPanelTab('exact')}
          >
            Exact Algorithm
          </button>
        </div>

        {activePanelTab === 'kill' ? (
          <section id="kill-switch-panel" className="control-tab-panel" role="tabpanel" aria-labelledby="kill-switch-tab">
            <p className="eyebrow">Emergency control</p>
            <h2>Feeder Kill Switch</h2>
            <p className="kill-switch-description">
              Trip the simulated feeder to mark every transformer OFF, including critical infrastructure.
            </p>
            <p className="kill-switch-notice">
              Dashboard simulation only. This does not operate physical grid equipment.
            </p>
            <button className="kill-switch-button" type="button" onClick={tripFeeder} disabled={feederIsOff}>
              {feederIsOff ? 'Feeder is shut down' : 'Trip Entire Feeder'}
            </button>
            {feederIsOff && <p className="kill-switch-status" role="status">{killSwitchMessage || 'All transformers are OFF.'}</p>}
          </section>
        ) : (
          <section
            id={`${activePanelTab}-panel`}
            className="control-tab-panel"
            role="tabpanel"
            aria-labelledby={`${activePanelTab}-tab`}
          >
        <p className="eyebrow">Feeder controls</p>
        <h2>
          {activePanelTab === 'quantum' ? 'Quantum Algorithm!' : activePanelTab === 'classical' ? 'Classical Algorithm' : 'Exact Algorithm'}
        </h2>

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

        <button className="dispatch-button" type="button" onClick={() => executeDispatch(activePanelTab)} disabled={dispatching || feederIsOff}>
          {dispatching ? 'Running dispatch…' : feederIsOff ? 'Feeder is shut down' : 'Execute Surgical Dispatch'}
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
          </section>
        )}
      </aside>
      </div>
    </div>
  )
}

export default App
