import { Link } from "@tanstack/react-router"
import { Button } from "@/components/ui/button"

const NotFound = () => {
  return (
    <div
      className="flex min-h-screen items-center justify-center flex-col p-4"
      data-testid="not-found"
    >
      <div className="flex items-center z-10">
        <div className="flex flex-col ml-4 items-center justify-center p-4">
          <span className="text-6xl md:text-8xl font-bold leading-none mb-4">
            404
          </span>
          <span className="text-2xl font-bold mb-2">页面不存在</span>
        </div>
      </div>

      <p className="text-lg text-muted-foreground mb-4 text-center z-10">
        这个地址没有对应页面，请检查链接或返回应用首页。
      </p>
      <div className="z-10">
        <Link to="/">
          <Button className="mt-4">返回应用首页</Button>
        </Link>
      </div>
    </div>
  )
}

export default NotFound
