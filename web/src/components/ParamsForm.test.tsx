import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ParamsForm, toPayload } from './ParamsForm'

const SCHEMA = {
  type: 'object',
  properties: {
    tree_folder_id: { type: 'string', title: 'Tree Folder Id', default: '' },
    confirm: { type: 'boolean', title: 'Confirm', default: false },
    workers: { type: 'integer', title: 'Workers', default: 4 },
    limit: { anyOf: [{ type: 'integer' }, { type: 'null' }], title: 'Limit', default: null },
  },
}

describe('ParamsForm', () => {
  it('renders one labeled input per property', () => {
    render(<ParamsForm schema={SCHEMA} values={{}} onChange={() => {}} />)
    expect(screen.getByLabelText('Tree Folder Id')).toBeTruthy()
    expect(screen.getByLabelText('Confirm')).toBeTruthy()
    expect(screen.getByLabelText('Workers')).toBeTruthy()
    expect(screen.getByLabelText('Limit')).toBeTruthy()
  })

  it('shows schema defaults in untouched fields', () => {
    render(<ParamsForm schema={SCHEMA} values={{}} onChange={() => {}} />)
    expect((screen.getByLabelText('Workers') as HTMLInputElement).value).toBe('4')
    expect((screen.getByLabelText('Confirm') as HTMLInputElement).checked).toBe(false)
    expect((screen.getByLabelText('Limit') as HTMLInputElement).value).toBe('')
  })

  it('reports edits through onChange', async () => {
    const onChange = vi.fn()
    render(<ParamsForm schema={SCHEMA} values={{}} onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Tree Folder Id'), 'x')
    expect(onChange).toHaveBeenCalledWith({ tree_folder_id: 'x' })
    await userEvent.click(screen.getByLabelText('Confirm'))
    expect(onChange).toHaveBeenCalledWith({ confirm: true })
  })

  it('renders nothing for an action without parameters', () => {
    const { container } = render(
      <ParamsForm schema={{ type: 'object', properties: {} }} values={{}} onChange={() => {}} />,
    )
    expect(container.innerHTML).toBe('')
  })
})

describe('toPayload', () => {
  it('omits untouched fields so server defaults apply', () => {
    expect(toPayload(SCHEMA, {})).toEqual({})
  })

  it('passes strings and booleans through', () => {
    expect(toPayload(SCHEMA, { tree_folder_id: 'abc123', confirm: true })).toEqual({
      tree_folder_id: 'abc123',
      confirm: true,
    })
  })

  it('coerces numeric text to numbers', () => {
    expect(toPayload(SCHEMA, { workers: '8' })).toEqual({ workers: 8 })
  })

  it('turns an emptied nullable field into null and drops an emptied plain one', () => {
    expect(toPayload(SCHEMA, { limit: '', workers: '' })).toEqual({ limit: null })
  })

  it('drops non-numeric text rather than sending it', () => {
    expect(toPayload(SCHEMA, { workers: 'lots' })).toEqual({})
  })
})
