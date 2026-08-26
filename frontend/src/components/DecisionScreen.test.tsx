import { render, screen, act } from '@testing-library/react';
import { vi, describe, test, expect, beforeEach, afterEach } from 'vitest';
import DecisionScreen from './DecisionScreen';
import type { BatchDecision } from '../types/schema';

const mockDecision: BatchDecision = {
  batch_id: 'DEMO-BATCH-001',
  status: 'ACCEPT',
  confidence: 0.96,
  timestamp: new Date().toISOString(),
  features: [
    { feature_name: 'temp_deviation', display_label: 'Temperature excursion', value: 0.05 },
    { feature_name: 'supplier_trend', display_label: 'Supplier trend deviation', value: 0.04 },
    { feature_name: 'viscosity_ph', display_label: 'Viscosity & pH drift', value: 0.07 },
    { feature_name: 'ocr_seal', display_label: 'OCR / Seal integrity', value: 0.01 },
  ],
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test('renders decision screen without crashing', () => {
  render(<DecisionScreen decision={mockDecision} />);

  // fast-forward past the 450ms loading state
  act(() => {
    vi.advanceTimersByTime(500);
  });

  expect(screen.getByText(/quality gate result/i)).toBeInTheDocument();
  expect(screen.getByText(/ACCEPT/i)).toBeInTheDocument();
  expect(screen.getByText(/96.0% Conf/i)).toBeInTheDocument();
});

test('shows HOLD styling for a HOLD decision', () => {
  const holdDecision = { ...mockDecision, status: 'HOLD' as const, confidence: 0.64 };
  render(<DecisionScreen decision={holdDecision} />);

  act(() => {
    vi.advanceTimersByTime(500);
  });

  expect(screen.getByText(/HOLD/i)).toBeInTheDocument();
});