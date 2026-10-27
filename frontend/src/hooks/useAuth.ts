import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  LoginService,
  type UserPublic,
  type UserRegister,
  UsersService,
} from "@/client"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const isLoggedIn = () => {
  return localStorage.getItem("access_token") !== null
}

const currentUserQuery = {
  queryKey: ["currentUser"],
  queryFn: async ({ signal }: { signal: AbortSignal }) =>
    (await UsersService.readUserMe({ signal })).data,
}

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const sessionToken = localStorage.getItem("access_token")

  const { data: user } = useQuery<UserPublic | null, Error>({
    ...currentUserQuery,
    enabled: isLoggedIn(),
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ body: data }),
    onSuccess: () => {
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      body: data,
    })
    await queryClient.cancelQueries()
    queryClient.clear()
    localStorage.setItem("access_token", response.data.access_token)
    try {
      // Resolve the new identity before mounting any account-scoped pages.
      await queryClient.fetchQuery(currentUserQuery)
    } catch (error) {
      localStorage.removeItem("access_token")
      queryClient.clear()
      throw error
    }
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = async () => {
    // A completed mutation from an older account must not log out a new session.
    if (localStorage.getItem("access_token") !== sessionToken) return
    localStorage.removeItem("access_token")
    // Cancel before clearing so late responses cannot repopulate the next session.
    await queryClient.cancelQueries()
    if (localStorage.getItem("access_token") !== null) return
    queryClient.clear()
    await navigate({ to: "/login" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user: isLoggedIn() ? user : null,
  }
}

export { isLoggedIn }
export default useAuth
