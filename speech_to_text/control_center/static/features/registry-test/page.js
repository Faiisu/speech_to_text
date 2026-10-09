export function mount(_root, {feature, logEvent}) {
  logEvent('Fixture mounted', feature.id, feature.id);
  return () => {};
}
