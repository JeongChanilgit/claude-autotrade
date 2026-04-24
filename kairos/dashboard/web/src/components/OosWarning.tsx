interface OosWarningProps {
  message: string;
}

export default function OosWarning({ message }: OosWarningProps) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-700 bg-red-950 text-red-200 px-4 py-3 text-sm"
    >
      <strong className="font-semibold">OOS warning: </strong>
      {message}
    </div>
  );
}
